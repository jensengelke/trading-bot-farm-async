"""
Telegram Notification Bot Implementation

Monitors the shared IB connection for orders, fills, and cancellations,
and sends real-time formatted notifications to Telegram.
"""

import asyncio
from typing import Any, Dict, Optional, Set
from ib_async import IB, Trade, Fill

from framework.bot_base import BotBase
from framework.decorators import trace_all_methods
from framework.telegram_client import TelegramClient
from bots.telegram.config import TelegramBotConfig
from bots.telegram.formatters import (
    format_order_placement,
    format_order_fill,
    format_order_cancelled,
    format_order_status_change,
    format_startup_message,
    format_connected_readiness_message
)


@trace_all_methods
class Bot(BotBase):
    """
    Telegram Notification Bot.
    
    Subscribes to shared IBKR order and execution events and dispatches
    real-time HTML alerts to Telegram.
    """
    
    def __init__(
        self,
        bot_id: str,
        config: Dict[str, Any],
        system_config: Any,
        ib_connection_manager: Any
    ):
        super().__init__(bot_id, config, system_config, ib_connection_manager)
        
        self.validated_config = TelegramBotConfig(**config)
        self.ib: Optional[IB] = None
        self._running = False
        self._events_registered = False
        
        self._notified_placements: Set[int] = set()
        self._known_order_statuses: Dict[int, str] = {}
        self._processed_executions: Set[str] = set()
        
        conn_name = self.validated_config.connection
        telegram_creds = self.system_config.get(f"telegram.connections.{conn_name}")
        
        if not telegram_creds:
            telegram_creds = self.system_config.get("telegram")
            if isinstance(telegram_creds, dict) and "connections" in telegram_creds:
                telegram_creds = telegram_creds["connections"].get(conn_name)

        if not telegram_creds or not isinstance(telegram_creds, dict):
            raise ValueError(
                f"Telegram connection '{conn_name}' not found in system configuration. "
                "Ensure 'telegram.connections' is configured in .secret-config.yaml"
            )
            
        token = telegram_creds.get("token")
        chat_id = telegram_creds.get("chat_id")
        if not token or not chat_id:
            raise ValueError(
                f"Incomplete Telegram credentials for connection '{conn_name}'. "
                "Both 'token' and 'chat_id' are required."
            )
            
        ssl_verify = telegram_creds.get("ssl_verify", True)
        self.telegram_client = TelegramClient(token, chat_id, logger=self.logger, ssl_verify=ssl_verify)
        self.logger.info(f"Initialized Telegram bot with connection '{conn_name}' for chat '{chat_id}'")

    async def start(self) -> None:
        """Start the Telegram notification bot."""
        self.logger.info(f"Starting Telegram notification bot: {self.bot_id}")
        self._running = True
        
        host = self.system_config.get("connection.host")
        port = self.system_config.get("connection.port")
        client_id = self.system_config.get("connection.client_id")
        ibc_config = self.system_config.get("ibc")
        
        self.ib = await self.ib_connection_manager.connect(host, port, client_id, ibc_config)
        self.logger.info("Connected to shared IBKR connection for order monitoring")
        
        try:
            self.ib.reqAutoOpenOrders(True)
            self.ib.reqAllOpenOrders()
            self.logger.info("Subscribed to all open orders on IB connection")
        except Exception as e:
            self.logger.warning(f"Could not request auto open orders: {e}")
            
        self._register_events()
        
        if not self.validated_config.silent_startup:
            try:
                await self._send_connected_readiness_message()
            except Exception as e:
                self.logger.warning(f"Could not send connected readiness message: {e}", exc_info=True)
            
        while self._running:
            await asyncio.sleep(1)

    async def _send_connected_readiness_message(self) -> None:
        """Fetch account balances, portfolio positions, and open orders, and send connected readiness alert."""
        if not self.ib or not self.ib.isConnected():
            self.logger.warning("Cannot send readiness message: IB is not connected")
            return

        target_account = self.system_config.get("connection.selected_account", "")
        if not target_account and self.validated_config.notifications.filter_accounts:
            target_account = self.validated_config.notifications.filter_accounts[0]

        # 1. Fetch Account Balances
        account_summary: Dict[str, Dict[str, str]] = {}
        try:
            summary_list = await asyncio.wait_for(
                self.ib.accountSummaryAsync(target_account),
                timeout=6.0
            )
            for av in summary_list:
                if not target_account or av.account == target_account:
                    account_summary[av.tag] = {"value": av.value, "currency": av.currency}
        except Exception as e:
            self.logger.warning(f"Could not fetch account summary: {e}")
            try:
                for av in self.ib.accountValues(target_account):
                    account_summary[av.tag] = {"value": av.value, "currency": av.currency}
            except Exception:
                pass

        # 2. Fetch Portfolio Positions
        positions: list = []
        try:
            items = self.ib.portfolio(target_account)
            if items:
                positions = items
            else:
                pos_list = await asyncio.wait_for(
                    self.ib.reqPositionsAsync(),
                    timeout=6.0
                )
                if target_account:
                    positions = [p for p in pos_list if p.account == target_account]
                else:
                    positions = pos_list
        except Exception as e:
            self.logger.warning(f"Could not fetch portfolio positions: {e}")
            try:
                positions = self.ib.positions(target_account)
            except Exception:
                positions = []

        # Filter out 0 quantity positions
        positions = [p for p in positions if getattr(p, "position", 0) != 0]

        # 3. Fetch Open Orders
        open_trades: list = []
        try:
            trades = await asyncio.wait_for(
                self.ib.reqAllOpenOrdersAsync(),
                timeout=6.0
            )
            open_trades = trades or self.ib.openTrades()
        except Exception as e:
            self.logger.warning(f"Could not fetch open orders: {e}")
            open_trades = self.ib.openTrades()

        filtered_trades = []
        for trade in open_trades:
            if target_account and trade.order.account and trade.order.account != target_account:
                continue
            status = trade.orderStatus.status if trade.orderStatus else ""
            if status not in ("Filled", "Cancelled", "ApiCancelled"):
                filtered_trades.append(trade)

        # 4. Format and dispatch
        text = format_connected_readiness_message(
            bot_id=self.bot_id,
            account=target_account,
            connection_name=self.validated_config.connection,
            account_summary=account_summary,
            positions=positions,
            open_trades=filtered_trades
        )
        self.logger.info(f"Sending connected readiness Telegram alert for account '{target_account}'")
        await self.telegram_client.send_message(text)

    async def stop(self) -> None:
        """Stop the Telegram notification bot."""
        self.logger.info(f"Stopping Telegram notification bot: {self.bot_id}")
        self._running = False
        self._unregister_events()
        self.cleanup()


    def _register_events(self) -> None:
        """Register listeners on IB events."""
        if not self.ib or self._events_registered:
            return
        self.ib.openOrderEvent += self._on_open_order
        self.ib.orderStatusEvent += self._on_order_status
        self.ib.execDetailsEvent += self._on_exec_details
        self._events_registered = True
        self.logger.info("Registered Telegram order event handlers")

    def _unregister_events(self) -> None:
        """Unregister listeners on IB events."""
        if not self.ib or not self._events_registered:
            return
        try:
            self.ib.openOrderEvent -= self._on_open_order
            self.ib.orderStatusEvent -= self._on_order_status
            self.ib.execDetailsEvent -= self._on_exec_details
        except Exception as e:
            self.logger.warning(f"Error unregistering IB events: {e}")
        self._events_registered = False

    def _should_filter_trade(self, trade: Trade) -> bool:
        """Check if trade matches configured account and orderRef filters."""
        rules = self.validated_config.notifications
        if not rules.enabled:
            return True
            
        order = trade.order
        acct = getattr(order, "account", "") or self.system_config.get("connection.selected_account", "")
        if rules.filter_accounts and acct not in rules.filter_accounts:
            return True
            
        order_ref = order.orderRef or ""
        if rules.exclude_order_refs:
            for pattern in rules.exclude_order_refs:
                if pattern in order_ref:
                    return True
                    
        if rules.include_order_refs:
            matched = any(pattern in order_ref for pattern in rules.include_order_refs)
            if not matched:
                return True
                
        return False

    def _on_open_order(self, trade: Trade) -> None:
        """Handle open order callback."""
        if not self._running or self._should_filter_trade(trade):
            return
            
        rules = self.validated_config.notifications
        order_id = trade.order.orderId
        
        if rules.notify_on_placement and order_id not in self._notified_placements:
            self._notified_placements.add(order_id)
            acct = self.system_config.get("connection.selected_account", "")
            text = format_order_placement(trade, account=acct)
            self.logger.info(f"Dispatching order placement Telegram notification for order {order_id}")
            self._dispatch_message(text)

    def _on_order_status(self, trade: Trade) -> None:
        """Handle order status transitions."""
        if not self._running or self._should_filter_trade(trade):
            return
            
        rules = self.validated_config.notifications
        order_id = trade.order.orderId
        status = trade.orderStatus.status if trade.orderStatus else "Unknown"
        old_status = self._known_order_statuses.get(order_id)
        
        if old_status == status:
            return
            
        self._known_order_statuses[order_id] = status
        acct = self.system_config.get("connection.selected_account", "")
        
        if status in ("Cancelled", "ApiCancelled") and rules.notify_on_cancellation:
            self.logger.info(f"Dispatching order cancellation Telegram alert for order {order_id}")
            text = format_order_cancelled(trade, account=acct)
            self._dispatch_message(text)
            return
            
        if rules.notify_on_status_change and old_status and status not in ("Filled", "Cancelled", "ApiCancelled"):
            self.logger.info(f"Dispatching order status update for order {order_id}: {old_status} -> {status}")
            text = format_order_status_change(trade, old_status, account=acct)
            self._dispatch_message(text)

    def _on_exec_details(self, trade: Trade, fill: Fill) -> None:
        """Handle order execution fill."""
        if not self._running or self._should_filter_trade(trade):
            return
            
        rules = self.validated_config.notifications
        if not rules.notify_on_fill:
            return
            
        exec_id = fill.execution.execId if fill.execution else None
        if exec_id and exec_id in self._processed_executions:
            return
            
        if exec_id:
            self._processed_executions.add(exec_id)
            
        acct = self.system_config.get("connection.selected_account", "")
        self.logger.info(f"Dispatching order fill Telegram notification for order {trade.order.orderId}")
        text = format_order_fill(trade, fill, account=acct)
        self._dispatch_message(text)

    def _dispatch_message(self, text: str) -> None:
        """Dispatch a message asynchronously on the event loop."""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.telegram_client.send_message(text))
        except RuntimeError:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self.telegram_client.send_message(text))
                else:
                    loop.run_until_complete(self.telegram_client.send_message(text))
            except Exception as e:
                self.logger.warning(f"Could not dispatch Telegram message: {e}")
