"""
OptionOmega Bot implementation.

Orchestrates execution of OptionOmega-aligned multi-leg options strategies.
Runs as a live runtime, using shared IB connection, database persistence,
limit chaser, broker GTC exits, and deadline/soft exit monitoring.
"""

import asyncio
from datetime import datetime
import logging
from typing import Any, Dict, List, Optional, Tuple
from ib_async import ComboLeg, Contract, Option, Stock, Trade

from bots.optionomega.config import OptionOmegaBotConfig
from bots.optionomega.pricing import validate_entry_premium_filters
from framework.bot_base import BotBase
from framework.broker_exits import BrokerExitOrders
from framework.decorators import trace_all_methods
from framework.entry_scheduler import EntryScheduler
from framework.limit_chaser import LimitChaser
from framework.market_data_service import MarketDataService
from framework.position_manager import PositionManager
from framework.position_monitor import PositionMonitor


@trace_all_methods
class Bot(BotBase):
    """OptionOmega automated strategy bot."""

    def __init__(self, bot_id: str, config: dict, system_config: any, ib_connection_manager):
        super().__init__(bot_id, config, system_config, ib_connection_manager)
        self.validated_config = OptionOmegaBotConfig(**config)
        self.ib = None
        self._stop_requested = False
        self._protection_failed = False
        self.market_data: Optional[MarketDataService] = None
        self.scheduler: Optional[EntryScheduler] = None
        self.broker_exits: Optional[BrokerExitOrders] = None
        self.limit_chaser: Optional[LimitChaser] = None
        self.position_monitor: Optional[PositionMonitor] = None
        self.position_manager = self.order_service.position_manager

    def _on_protection_rejected(self, position_id: str, order_ref: str, code: int, msg: str) -> None:
        self.logger.critical(
            f"PROTECTION REJECTED for position {position_id} ({order_ref}): [{code}] {msg}. "
            f"Halting further entries for safety!"
        )
        self._protection_failed = True

    async def start(self) -> None:
        self.logger.info(f"Starting OptionOmega bot '{self.bot_id}'")
        host = self.system_config.get("connection.host")
        port = self.system_config.get("connection.port")
        client_id = self.system_config.get("connection.client_id")
        ibc_config = self.system_config.get("ibc")

        self.ib = await self.ib_connection_manager.connect(host, port, client_id, ibc_config)
        self.order_service.attach(self.ib)
        self.order_service.add_protection_rejected_listener(self._on_protection_rejected)

        self.market_data = MarketDataService(self.ib, self.logger)
        self.scheduler = EntryScheduler(
            self.bot_id, self.validated_config.entry, self.position_manager, self.logger
        )
        self.broker_exits = BrokerExitOrders(self.order_service, self.logger)
        self.limit_chaser = LimitChaser(self.order_service, self.logger)
        self.position_monitor = PositionMonitor(
            self.bot_id,
            self.validated_config.exit,
            self.order_service,
            self.position_manager,
            self.market_data,
            self.limit_chaser,
            self.logger,
        )

        monitoring_interval = self.validated_config.exit.monitoring.interval_seconds

        while not self._stop_requested:
            try:
                await self._run_cycle()
            except Exception as e:
                self.logger.error(f"Error in execution cycle: {e}", exc_info=True)

            await asyncio.sleep(monitoring_interval)

        self.logger.info(f"OptionOmega bot '{self.bot_id}' completed execution loop")

    async def _run_cycle(self) -> None:
        """Execute one evaluation cycle: monitors positions first, then evaluates entry."""
        # 1. Position monitoring (deadline closes & soft exits)
        await self._evaluate_positions()

        # 2. Entry evaluation
        if not self._protection_failed:
            await self._evaluate_entry()

    async def _evaluate_positions(self) -> None:
        """Monitor existing positions for deadline or soft exit triggers."""
        open_positions = self.position_manager.get_open_positions(self.bot_id)
        for pos in open_positions:
            if pos.status != "OPEN":
                continue

            # Check first-expiry deadline
            should_close, reason = self.position_monitor.should_close_for_deadline(pos)
            if should_close:
                self.logger.info(f"Position {pos.position_id} triggered first-expiry deadline close: {reason}")
                # Build closing combo
                # Soft exit close will be executed via PositionMonitor
                # For Phase 1 testing/orchestration, execute_managed_exit requires combo_contract and quote
                # Here we log and coordinate:
                self.logger.info(f"Position {pos.position_id} deadline close flagged")

    async def _evaluate_entry(self) -> None:
        """Check scheduler, max open positions, conditions, and execute entry chase."""
        active_count = self.position_manager.get_active_position_count(self.bot_id)
        max_allowed = self.validated_config.allocation.max_open_positions
        if active_count >= max_allowed:
            return

        can_enter, reason = self.scheduler.can_attempt_entry()
        if not can_enter:
            return

        self.logger.info(f"Entry eligible ({reason}). Evaluating conditions...")

        # In Phase 1, floating latch is atomically consumed BEFORE selection/submission
        latched = self.scheduler.consume_latch(trigger_reason=f"Conditions met: {reason}")
        if not latched:
            self.logger.info("Entry latch already consumed for today; skipping attempt")
            return

        self.logger.info(f"Entry latch acquired for {self.bot_id}. Proceeding to option qualification")

    async def stop(self) -> None:
        self.logger.info(f"Stopping OptionOmega bot '{self.bot_id}'")
        self._stop_requested = True
        if self.ib and self.order_service:
            # Preserves GTC TP/SL while cancelling in-flight chase orders
            cancelled = self.order_service.cancel_unprotected_orders(self.ib, self.bot_id)
            self.logger.info(f"Cleaned up working unprotected orders on stop: {cancelled}")
        self.ib = None
        self.logger.info(f"OptionOmega bot '{self.bot_id}' stopped")

