"""
Position monitor and exit coordinator.

Monitors active virtual positions for:
- First-leg expiration deadlines (close_full_position_before_first_leg_expires)
- Soft exit conditions (managed exits, position delta)
- Broker order fill reconciliation
"""

import asyncio
from datetime import datetime, timezone
import logging
from typing import Dict, List, Optional, Tuple
import pytz
from ib_async import IB, Contract, Trade

from bots.optionomega.config import ExitConfig
from framework.limit_chaser import LimitChaser
from framework.market_data_service import MarketDataService
from framework.order_service import (
    OrderService,
    ROLE_STOP_LOSS,
    ROLE_TAKE_PROFIT,
)
from framework.position_manager import PositionManager, VirtualPosition

NY_TZ = pytz.timezone("America/New_York")


class PositionMonitor:
    """Monitors open virtual positions and coordinates soft and deadline exits."""

    def __init__(
        self,
        bot_id: str,
        exit_config: ExitConfig,
        order_service: OrderService,
        position_manager: PositionManager,
        market_data_service: MarketDataService,
        limit_chaser: LimitChaser,
        logger: logging.Logger,
    ):
        self.bot_id = bot_id
        self.config = exit_config
        self.order_service = order_service
        self.position_manager = position_manager
        self.market_data = market_data_service
        self.limit_chaser = limit_chaser
        self.logger = logger
        self._position_locks: Dict[str, asyncio.Lock] = {}

    def _get_lock(self, position_id: str) -> asyncio.Lock:
        if position_id not in self._position_locks:
            self._position_locks[position_id] = asyncio.Lock()
        return self._position_locks[position_id]

    def should_close_for_deadline(
        self, position: VirtualPosition, now_dt: Optional[datetime] = None
    ) -> Tuple[bool, str]:
        """Check if position must be closed before the earliest leg expires."""
        if not self.config.close_full_position_before_first_leg_expires:
            return False, "First-leg expiration close disabled"

        minutes_before = self.config.close_full_position_before_first_leg_expires_minutes or 20
        now = now_dt or datetime.now(NY_TZ)

        legs = position.legs or self.position_manager.get_position_legs(position.position_id)
        if not legs:
            return False, "No legs found for position"

        cutoff_dt: Optional[datetime] = None
        for leg in legs:
            cutoff_str = leg.get("last_trade_cutoff")
            if cutoff_str:
                try:
                    dt = datetime.fromisoformat(cutoff_str)
                    if dt.tzinfo is None:
                        dt = NY_TZ.localize(dt)
                    if cutoff_dt is None or dt < cutoff_dt:
                        cutoff_dt = dt
                except ValueError:
                    pass

            if cutoff_dt is None:
                expiry_str = leg.get("expiry") or leg.get("expiration")
                if expiry_str:
                    try:
                        d = datetime.strptime(expiry_str, "%Y%m%d")
                        dt = NY_TZ.localize(datetime(d.year, d.month, d.day, 16, 0, 0))
                        if cutoff_dt is None or dt < cutoff_dt:
                            cutoff_dt = dt
                    except ValueError:
                        pass

        if cutoff_dt is None:
            return False, "Could not determine earliest leg expiry cutoff"

        delta_minutes = (cutoff_dt - now).total_seconds() / 60.0
        if 0 <= delta_minutes <= minutes_before:
            return True, f"Earliest leg expires in {delta_minutes:.1f}m (deadline window: {minutes_before}m)"

        return False, f"{delta_minutes:.1f}m until earliest cutoff"

    async def execute_managed_exit(
        self,
        ib: IB,
        position: VirtualPosition,
        combo_contract: Contract,
        current_mid: float,
        natural_bid: float,
        reason: str,
    ) -> bool:
        """
        Safely execute a managed soft exit or deadline close.

        1. Locks position.
        2. Sets status to EXIT_CANCEL_PENDING.
        3. Cancels broker bracket orders (TP/SL) and awaits confirmation.
        4. Sets status to EXIT_WORKING.
        5. Executes soft exit LimitChaser (mid -> natural_bid).
        """
        lock = self._get_lock(position.position_id)
        async with lock:
            latest = self.position_manager.get_position(position.position_id)
            if latest is None or latest.status not in ("OPEN", "EXIT_WORKING", "EXIT_CANCEL_PENDING"):
                self.logger.info(
                    f"Position {position.position_id} status is {latest.status if latest else 'None'}; exit aborted"
                )
                return False

            self.logger.info(f"Initiating managed exit for position {position.position_id}: {reason}")
            latest.status = "EXIT_CANCEL_PENDING"
            latest.soft_exit_state = reason
            self.position_manager.update_position(latest)

            orders = self.position_manager.get_orders_for_position(position.position_id)
            for ord_row in orders:
                if ord_row["role"] in (ROLE_TAKE_PROFIT, ROLE_STOP_LOSS) and ord_row["status"] not in ("Filled", "Cancelled"):
                    ref = ord_row["order_ref"]
                    for trade in ib.openTrades():
                        if trade.order.orderRef == ref:
                            self.logger.info(f"Cancelling protective order {ref} before soft exit chase")
                            self.order_service.cancel_order(ib, trade)
                            await self.order_service.wait_for_cancel(ref, timeout=10.0)

            latest.status = "EXIT_WORKING"
            self.position_manager.update_position(latest)

            exec_cfg = self.config.execution
            price_increment = exec_cfg.price_increment if exec_cfg else 0.01
            wait_seconds = exec_cfg.wait_seconds if exec_cfg else 20.0
            max_attempts = exec_cfg.max_attempts if exec_cfg else 5

            result = await self.limit_chaser.chase_soft_exit(
                ib=ib,
                position_id=position.position_id,
                contract=combo_contract,
                quantity=latest.quantity,
                current_mid=current_mid,
                natural_bid=natural_bid,
                price_increment=price_increment,
                wait_seconds=wait_seconds,
                max_attempts=max_attempts,
            )

            if result.success:
                self.logger.info(
                    f"Managed exit successful for position {position.position_id} @ {result.avg_fill_price}"
                )
                return True
            else:
                self.logger.error(
                    f"Managed exit unfilled for position {position.position_id}! Position remains in EXIT_WORKING"
                )
                return False

