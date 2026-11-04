"""
Telegram Notification Bot Implementation

Monitors the shared IB connection for:
- Connection / disconnection state changes
- Order placement, status transitions, and cancellations
- Execution fills and commissions
- Daily account status summary
"""

import asyncio
from datetime import datetime
from typing import Any, Dict, Optional, Set
import pytz
from ib_async import IB, Trade, Fill

from framework.bot_base import BotBase
from framework.decorators import trace_all_methods
from framework.telegram_client import TelegramClient, get_telegram_client
from framework.account_service import fetch_account_snapshot
from framework.formatters import (
    format_connection_state_change,
    format_order_placement,
    format_order_fill,
    format_order_cancelled,
    format_order_status_change,
    format_connected_readiness_message,
    format_daily_account_summary,
    format_startup_message
)
from bots.telegram.config import TelegramBotConfig


@trace_all_methods
class Bot(BotBase):
    """
    Telegram Notification Bot.
    
    Subscribes to shared IBKR order, execution, and connection events,
    and dispatches real-time HTML alerts to Telegram.
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
        self._daily_summary_task: Optional[asyncio.Task] = None
        
        # Initialize Telegram client via framework factory
        self.telegram_client = get_telegram_client(
            self.system_config,
            connection_name=self.validated_config.connection,
            logger=self.logger
        )
        self.logger.info(
            f"Initialized Telegram bot with connection '{self.validated_config.connection}'"
        )

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
            
        if self.validated_config.daily_summary and self.validated_config.daily_summary.enabled:
            self._daily_summary_task = asyncio.create_task(self._daily_summary_loop())
            self.logger.info(
                f"Started daily account summary scheduler for {self.validated_config.daily_summary.time} "
                f"({self.validated_config.daily_summary.timezone})"
            )

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

        summary, positions, open_trades = await fetch_account_snapshot(
            self.ib, account=target_account, logger=self.logger
        )

        text = format_connected_readiness_message(
            bot_id=self.bot_id,
            account=target_account,
            connection_name=self.validated_config.connection,
            account_summary=summary,
            positions=positions,
            open_trades=open_trades
        )
        self.logger.info(f"Sending connected readiness Telegram alert for account '{target_account}'")
        await self.telegram_client.send_message(text)

    async def _send_daily_summary(self) -> None:
        """Fetch snapshot and send daily account summary broadcast."""
        if not self.ib or not self.ib.isConnected():
            self.logger.warning("Cannot send daily summary: IB is not connected")
            return

        target_account = self.system_config.get("connection.selected_account", "")
        summary, positions, open_trades = await fetch_account_snapshot(
            self.ib, account=target_account, logger=self.logger
        )

        text = format_daily_account_summary(
            account=target_account,
            account_summary=summary,
            pos_count=len(positions),
            order_count=len(open_trades)
        )
        self.logger.info(f"Sending daily account summary Telegram alert for account '{target_account}'")
        await self.telegram_client.send_message(text)

    async def _daily_summary_loop(self) -> None:
        """Background loop monitoring scheduled time for daily account summary."""
        cfg = self.validated_config.daily_summary
        if not cfg or not cfg.enabled:
            return

        tz = pytz.timezone(cfg.timezone)
        last_sent_date = None

        while self._running:
            try:
                now = datetime.now(tz)
                current_time_str = now.strftime("%H:%M")
                current_date_str = now.strftime("%Y-%m-%d")

                if current_time_str == cfg.time and last_sent_date != current_date_str:
                    last_sent_date = current_date_str
                    await self._send_daily_summary()

                await asyncio.sleep(25)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error in daily summary loop: {e}", exc_info=True)
                await asyncio.sleep(30)

    async def stop(self) -> None:
        """Stop the Telegram notification bot."""
        self.logger.info(f"Stopping Telegram notification bot: {self.bot_id}")
        self._running = False
        if self._daily_summary_task and not self._daily_summary_task.done():
            self._daily_summary_task.cancel()
            try:
                await self._daily_summary_task
            except asyncio.CancelledError:
                pass
        self._unregister_events()
        self.cleanup()

    def _register_events(self) -> None:
        """Register listeners on IB events."""
        if not self.ib or self._events_registered:
            return
        self.ib.connectedEvent += self._on_connected
        self.ib.disconnectedEvent += self._on_disconnected
        self.ib.openOrderEvent += self._on_open_order
        self.ib.orderStatusEvent += self._on_order_status
        self.ib.execDetailsEvent += self._on_exec_details
        self._events_registered = True
        self.logger.info("Registered Telegram order and connection event handlers")

    def _unregister_events(self) -> None:
        """Unregister listeners on IB events."""
        if not self.ib or not self._events_registered:
            return
        try:
            self.ib.connectedEvent -= self._on_connected
            self.ib.disconnectedEvent -= self._on_disconnected
            self.ib.openOrderEvent -= self._on_open_order
            self.ib.orderStatusEvent -= self._on_order_status
            self.ib.execDetailsEvent -= self._on_exec_details
        except Exception as e:
            self.logger.warning(f"Error unregistering IB events: {e}")
        self._events_registered = False

    def _on_connected(self) -> None:
        """Handle IB connection established event."""
        if not self._running:
            return
        self.logger.info("IBKR connection established event received")
        if self.validated_config.notifications.notify_on_connection:
            host = self.system_config.get("connection.host", "")
            port = self.system_config.get("connection.port", 0)
            acct = self.system_config.get("connection.selected_account", "")
            text = format_connection_state_change(
                connected=True, host=str(host), port=int(port or 0), account=str(acct)
            )
            self._dispatch_message(text)

    def _on_disconnected(self) -> None:
        """Handle IB connection dropped event."""
        if not self._running:
            return
        self.logger.warning("IBKR connection dropped event received")
        if self.validated_config.notifications.notify_on_connection:
            host = self.system_config.get("connection.host", "")
            port = self.system_config.get("connection.port", 0)
            acct = self.system_config.get("connection.selected_account", "")
            text = format_connection_state_change(
                connected=False,
                host=str(host),
                port=int(port or 0),
                account=str(acct),
                details="Connection to Gateway/TWS dropped"
            )
            self._dispatch_message(text)

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
