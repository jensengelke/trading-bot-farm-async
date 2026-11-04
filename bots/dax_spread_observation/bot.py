"""
DAX Spread Observation Bot Implementation.

Monitors manual DAX option spreads on IBKR:
- Short leg delta growth above threshold (e.g. 0.38)
- Short strike tested / breached
- Long strike tested / breached
- Scheduled daily summary of all positions, unrealized P&L, TP orders, and strike buffers
"""

import asyncio
from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional, Set
import pytz
from ib_async import IB, Contract, Index, Trade

from framework.bot_base import BotBase
from framework.decorators import trace_all_methods
from framework.telegram_client import TelegramClient, get_telegram_client
from framework.market_data_service import MarketDataService
from framework.option_spreads import (
    OptionSpreadPosition,
    identify_spread_positions,
    audit_take_profit_orders
)
from bots.dax_spread_observation.config import DaxSpreadObservationConfig
from bots.dax_spread_observation.formatters import (
    format_delta_breach_alert,
    format_strike_tested_alert,
    format_daily_spread_summary
)


@trace_all_methods
class Bot(BotBase):
    """
    DAX Spread Observation Bot.
    
    Identifies and continuously monitors manual DAX Bear Call Spreads and Bull Put Spreads.
    """
    
    def __init__(
        self,
        bot_id: str,
        config: Dict[str, Any],
        system_config: Any,
        ib_connection_manager: Any
    ):
        super().__init__(bot_id, config, system_config, ib_connection_manager)
        
        self.validated_config = DaxSpreadObservationConfig(**config)
        self.ib: Optional[IB] = None
        self._running = False
        self.market_data: Optional[MarketDataService] = None
        self.underlying_contract: Optional[Contract] = None
        
        # Alert cooldown tracking: alert_key -> timestamp
        self._last_alert_times: Dict[str, datetime] = {}
        # Daily summary tracking: (date_str, slot_time_str) -> bool
        self._sent_daily_summaries: Set[str] = set()
        
        # Subscribed option contract IDs
        self._subscribed_con_ids: Set[int] = set()

        # Initialize Telegram client
        self.telegram_client = get_telegram_client(
            self.system_config,
            connection_name=self.validated_config.connection,
            logger=self.logger
        )
        self.logger.info(
            f"Initialized DAX Spread Observation Bot '{self.bot_id}' "
            f"using Telegram connection '{self.validated_config.connection}'"
        )

    async def start(self) -> None:
        """Start the DAX Spread Observation bot."""
        self.logger.info(f"Starting DAX Spread Observation bot: {self.bot_id}")
        self._running = True

        host = self.system_config.get("connection.host")
        port = self.system_config.get("connection.port")
        client_id = self.system_config.get("connection.client_id")
        ibc_config = self.system_config.get("ibc")

        self.ib = await self.ib_connection_manager.connect(host, port, client_id, ibc_config)
        self.logger.info("Connected to shared IBKR connection")

        try:
            self.ib.reqAutoOpenOrders(True)
            self.ib.reqAllOpenOrders()
            self.logger.info("Subscribed to auto open orders on IB connection")
        except Exception as e:
            self.logger.warning(f"Could not request auto open orders: {e}")

        # Set market data type (e.g. 3 for delayed if Eurex API subscription is delayed)
        try:
            self.ib.reqMarketDataType(self.validated_config.market_data_type)
            self.logger.info(f"Set IB market data type to {self.validated_config.market_data_type}")
        except Exception as e:
            self.logger.warning(f"Could not set market data type: {e}")

        self.market_data = MarketDataService(self.ib, self.logger)

        # Resolve DAX underlying index
        await self._resolve_underlying()

        self.logger.info(
            f"Monitoring loop active (cadence: {self.validated_config.monitoring_interval_seconds}s, "
            f"max_short_delta: {self.validated_config.max_short_delta}, "
            f"tested_buffer: {self.validated_config.strike_tested_buffer_points} pts)"
        )

        while self._running:
            try:
                await self._monitor_cycle()
            except Exception as e:
                self.logger.error(f"Error in DAX monitoring cycle: {e}", exc_info=True)

            await asyncio.sleep(self.validated_config.monitoring_interval_seconds)

    async def stop(self) -> None:
        """Stop the DAX Spread Observation bot."""
        self.logger.info(f"Stopping DAX Spread Observation bot: {self.bot_id}")
        self._running = False

        if self.market_data and self.underlying_contract:
            try:
                self.market_data.unsubscribe(self.underlying_contract)
            except Exception:
                pass

        self.cleanup()

    async def _resolve_underlying(self) -> None:
        """Resolve and qualify the DAX Index underlying contract."""
        cfg = self.validated_config
        candidates = [
            Contract(conId=825711, exchange=cfg.exchange, secType="IND", symbol=cfg.symbol, currency=cfg.currency),
            Index(cfg.symbol, cfg.exchange, cfg.currency),
            Index(cfg.symbol, "DTB", cfg.currency),
            Index(cfg.symbol, "", cfg.currency)
        ]
        for c in candidates:
            try:
                qualified = await asyncio.wait_for(
                    self.ib.qualifyContractsAsync(c),
                    timeout=5.0
                )
                if qualified and qualified[0] and qualified[0].conId > 0:
                    self.underlying_contract = qualified[0]
                    self.market_data.subscribe(self.underlying_contract)
                    self.logger.info(
                        f"Resolved DAX underlying: conId={self.underlying_contract.conId}, "
                        f"exchange={self.underlying_contract.exchange}"
                    )
                    return
            except Exception as e:
                self.logger.debug(f"Could not qualify candidate {c}: {e}")

        self.logger.warning("Could not qualify DAX index contract; will rely on option model prices")

    def _get_underlying_price(self) -> Optional[float]:
        """Get latest market price of the underlying DAX index."""
        if self.underlying_contract and self.market_data:
            snap = self.market_data.get_quote(self.underlying_contract)
            if snap.last and snap.last > 0:
                return snap.last
            if snap.mid and snap.mid > 0:
                return snap.mid

        # Fallback to ticker directly
        if self.underlying_contract:
            con_id = self.underlying_contract.conId
            ticker = self.market_data._tickers.get(con_id)
            if ticker:
                p = ticker.marketPrice()
                if p and p > 0:
                    return p
                if ticker.close and ticker.close > 0:
                    return ticker.close
        return None

    def _can_alert(self, alert_key: str) -> bool:
        """Check if an alert can be sent without violating the cooldown period."""
        now = datetime.now(timezone.utc)
        last_time = self._last_alert_times.get(alert_key)
        if not last_time:
            return True
        elapsed_min = (now - last_time).total_seconds() / 60.0
        return elapsed_min >= self.validated_config.alert_cooldown_minutes

    def _mark_alert_sent(self, alert_key: str) -> None:
        """Mark an alert key as sent at the current time."""
        self._last_alert_times[alert_key] = datetime.now(timezone.utc)

    async def _monitor_cycle(self) -> None:
        """Execute one evaluation cycle."""
        if not self.ib or not self.ib.isConnected():
            return

        target_account = self.validated_config.account or self.system_config.get("connection.selected_account", "")

        # 1. Fetch live portfolio and working orders
        portfolio = self.ib.portfolio(target_account) if target_account else self.ib.portfolio()
        open_trades = self.ib.openTrades()

        # 2. Identify spreads and audit take-profit orders
        spreads = identify_spread_positions(list(portfolio), symbol=self.validated_config.symbol)
        audit_take_profit_orders(spreads, open_trades)

        if not spreads:
            self.logger.debug(f"No active {self.validated_config.symbol} spreads identified in portfolio")
            await self._check_daily_summary(spreads, None, target_account)
            return

        # 3. Ensure market data subscriptions for short & long legs
        to_qualify = []
        for spread in spreads:
            s_c = spread.short_leg.contract
            l_c = spread.long_leg.contract
            for c in (s_c, l_c):
                if not getattr(c, "exchange", None):
                    c.exchange = self.validated_config.exchange
                if c.conId not in self._subscribed_con_ids:
                    to_qualify.append(c)

        if to_qualify:
            try:
                await asyncio.wait_for(self.ib.qualifyContractsAsync(*to_qualify), timeout=5.0)
            except Exception as e:
                self.logger.debug(f"Error qualifying option contracts: {e}")

            for c in to_qualify:
                self.market_data.subscribe(c)
                self._subscribed_con_ids.add(c.conId)

            # Wait for delayed / streaming Greeks to populate
            await asyncio.sleep(2.0)

        # 4. Get current underlying price
        und_price = self._get_underlying_price()

        # 5. Evaluate each spread
        for spread in spreads:
            s_contract = spread.short_leg.contract
            s_snap = self.market_data.get_quote(s_contract)
            
            # Populate underlying price if not yet set
            if und_price:
                spread.underlying_price = und_price
            elif s_snap.delta and hasattr(s_contract, "underlyingPrice"):
                spread.underlying_price = getattr(s_contract, "underlyingPrice", None)
            else:
                ticker = self.market_data._tickers.get(s_contract.conId)
                if ticker and ticker.modelGreeks and ticker.modelGreeks.undPrice:
                    spread.underlying_price = ticker.modelGreeks.undPrice
                    und_price = spread.underlying_price

            # Populate delta
            if s_snap.delta is not None:
                spread.short_delta = s_snap.delta
            else:
                ticker = self.market_data._tickers.get(s_contract.conId)
                if ticker and ticker.modelGreeks and ticker.modelGreeks.delta is not None:
                    spread.short_delta = ticker.modelGreeks.delta

            # Evaluation A: Short Leg Delta Growth
            if spread.short_delta is not None:
                abs_delta = abs(spread.short_delta)
                if abs_delta > self.validated_config.max_short_delta:
                    alert_key = f"delta_{spread.expiration}_{spread.right}_{spread.short_strike}"
                    if self._can_alert(alert_key):
                        self.logger.warning(
                            f"Short delta breach on {spread.expiration} {spread.short_strike}{spread.right}: "
                            f"{abs_delta:.3f} > {self.validated_config.max_short_delta}"
                        )
                        msg = format_delta_breach_alert(
                            spread,
                            short_delta=spread.short_delta,
                            threshold=self.validated_config.max_short_delta
                        )
                        self._mark_alert_sent(alert_key)
                        asyncio.create_task(self.telegram_client.send_message(msg))

            # Evaluation B: Short Strike Tested
            if spread.underlying_price:
                buf_pts = spread.short_strike_buffer_points
                if buf_pts is not None and spread.is_short_strike_tested(self.validated_config.strike_tested_buffer_points):
                    alert_key = f"short_tested_{spread.expiration}_{spread.right}_{spread.short_strike}"
                    if self._can_alert(alert_key):
                        self.logger.warning(
                            f"Short strike tested on {spread.expiration} {spread.short_strike}{spread.right}: "
                            f"Buffer={buf_pts:.1f} pts (DAX={spread.underlying_price:.1f})"
                        )
                        msg = format_strike_tested_alert(
                            spread,
                            is_long=False,
                            strike=spread.short_strike,
                            underlying_price=spread.underlying_price,
                            buffer_pts=buf_pts
                        )
                        self._mark_alert_sent(alert_key)
                        asyncio.create_task(self.telegram_client.send_message(msg))

            # Evaluation C: Long Strike Tested (Max Loss Zone)
            if spread.underlying_price:
                long_buf = spread.long_strike_buffer_points
                if long_buf is not None and spread.is_long_strike_tested():
                    alert_key = f"long_tested_{spread.expiration}_{spread.right}_{spread.long_strike}"
                    if self._can_alert(alert_key):
                        self.logger.critical(
                            f"LONG STRIKE TESTED on {spread.expiration} {spread.long_strike}{spread.right}: "
                            f"Buffer={long_buf:.1f} pts"
                        )
                        msg = format_strike_tested_alert(
                            spread,
                            is_long=True,
                            strike=spread.long_strike,
                            underlying_price=spread.underlying_price,
                            buffer_pts=long_buf
                        )
                        self._mark_alert_sent(alert_key)
                        asyncio.create_task(self.telegram_client.send_message(msg))

        # Build contract map for combo leg resolution
        con_map = {getattr(p.contract, "conId", 0): p.contract for p in portfolio if getattr(p, "contract", None)}

        # 6. Check daily summary schedule
        await self._check_daily_summary(
            spreads=spreads,
            underlying_price=und_price,
            account=target_account,
            open_trades=open_trades,
            contract_map=con_map
        )

    async def _check_daily_summary(
        self,
        spreads: List[OptionSpreadPosition],
        underlying_price: Optional[float],
        account: str,
        open_trades: Optional[List[Trade]] = None,
        contract_map: Optional[Dict[int, Any]] = None
    ) -> None:
        """Check if current time matches scheduled daily summary time slot."""
        cfg = self.validated_config
        if not cfg.daily_summary_schedule:
            return

        tz = pytz.timezone(cfg.timezone)
        now = datetime.now(tz)
        current_time_str = now.strftime("%H:%M")
        current_date_str = now.strftime("%Y-%m-%d")

        for slot in cfg.daily_summary_schedule:
            if current_time_str == slot:
                slot_key = f"{current_date_str}_{slot}"
                if slot_key not in self._sent_daily_summaries:
                    self._sent_daily_summaries.add(slot_key)
                    self.logger.info(f"Triggering scheduled daily DAX spread summary ({slot})")
                    msg = format_daily_spread_summary(
                        spreads=spreads,
                        underlying_price=underlying_price,
                        account=account,
                        open_trades=open_trades,
                        contract_map=contract_map
                    )
                    await self.telegram_client.send_message(msg)



