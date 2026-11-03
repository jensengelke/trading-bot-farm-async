"""
Bounded limit chaser for entries and soft exits.

Entries: Chases from mid within max_adverse_price_change and premium bounds.
Soft exits: Chases from mid toward natural side, never beyond it.
"""

import asyncio
from dataclasses import dataclass
import logging
from typing import Optional
from ib_async import IB, Contract, LimitOrder, Trade

from bots.optionomega.pricing import constrain_entry_limit, round_to_tick
from framework.order_service import OrderService, ROLE_CLOSE, ROLE_OPEN


@dataclass
class ChaseResult:
    success: bool
    trade: Trade
    filled_quantity: int
    avg_fill_price: Optional[float]
    status: str
    message: str


class LimitChaser:
    """Iterative limit order chaser with strict price budgets and bounds."""

    def __init__(self, order_service: OrderService, logger: logging.Logger):
        self.order_service = order_service
        self.logger = logger

    async def chase_entry(
        self,
        ib: IB,
        position_id: str,
        contract: Contract,
        quantity: int,
        anchor_mid: float,
        price_increment: float,
        wait_seconds: float,
        max_attempts: int,
        max_adverse_price_change: float,
        starting_offset: float = 0.0,
        min_credit: Optional[float] = None,
        max_credit: Optional[float] = None,
        min_debit: Optional[float] = None,
        max_debit: Optional[float] = None,
        oo_min: Optional[float] = None,
        oo_max: Optional[float] = None,
        tick_size: float = 0.01,
    ) -> ChaseResult:
        """Execute an entry limit chase (canonical BUY)."""
        candidate = anchor_mid + starting_offset
        limit_price = constrain_entry_limit(
            candidate_limit=candidate,
            anchor_mid=anchor_mid,
            max_adverse_price_change=max_adverse_price_change,
            min_credit=min_credit,
            max_credit=max_credit,
            min_debit=min_debit,
            max_debit=max_debit,
            oo_min=oo_min,
            oo_max=oo_max,
            tick_size=tick_size,
        )

        order = LimitOrder(action="BUY", totalQuantity=quantity, lmtPrice=limit_price)
        trade = self.order_service.place_order(
            ib=ib,
            position_id=position_id,
            contract=contract,
            order=order,
            role=ROLE_OPEN,
            reference_price=anchor_mid,
        )

        attempt = 1
        self.logger.info(
            f"Entry chase started attempt {attempt}/{max_attempts} @ {limit_price} (anchor: {anchor_mid})"
        )

        while attempt <= max_attempts:
            await asyncio.sleep(wait_seconds)

            if trade.orderStatus.status == "Filled":
                return ChaseResult(
                    success=True,
                    trade=trade,
                    filled_quantity=int(trade.orderStatus.filled),
                    avg_fill_price=float(trade.orderStatus.avgFillPrice),
                    status=trade.orderStatus.status,
                    message="Filled completely",
                )

            if trade.orderStatus.status in ("Cancelled", "Inactive"):
                return ChaseResult(
                    success=False,
                    trade=trade,
                    filled_quantity=int(trade.orderStatus.filled),
                    avg_fill_price=float(trade.orderStatus.avgFillPrice) if trade.orderStatus.filled else None,
                    status=trade.orderStatus.status,
                    message=f"Order ended in {trade.orderStatus.status}",
                )

            attempt += 1
            if attempt > max_attempts:
                break

            next_candidate = round_to_tick(limit_price + price_increment, tick_size)
            try:
                next_limit = constrain_entry_limit(
                    candidate_limit=next_candidate,
                    anchor_mid=anchor_mid,
                    max_adverse_price_change=max_adverse_price_change,
                    min_credit=min_credit,
                    max_credit=max_credit,
                    min_debit=min_debit,
                    max_debit=max_debit,
                    oo_min=oo_min,
                    oo_max=oo_max,
                    tick_size=tick_size,
                )
            except ValueError as e:
                self.logger.warning(f"Entry chase hit budget/bound limit: {e}")
                break

            if next_limit != limit_price:
                limit_price = next_limit
                self.logger.info(
                    f"Entry chase modifying attempt {attempt}/{max_attempts} to limit {limit_price}"
                )
                self.order_service.modify_order(ib, trade, limit_price=limit_price)

        self.logger.warning(
            f"Entry chase exhausted {max_attempts} attempts; cancelling order {trade.order.orderRef}"
        )
        self.order_service.cancel_order(ib, trade)
        await self.order_service.wait_for_cancel(trade.order.orderRef, timeout=wait_seconds)

        filled = int(trade.orderStatus.filled)
        return ChaseResult(
            success=filled > 0,
            trade=trade,
            filled_quantity=filled,
            avg_fill_price=float(trade.orderStatus.avgFillPrice) if filled else None,
            status=trade.orderStatus.status,
            message=f"Chase timed out after {max_attempts} attempts (filled {filled}/{quantity})",
        )

    async def chase_soft_exit(
        self,
        ib: IB,
        position_id: str,
        contract: Contract,
        quantity: int,
        current_mid: float,
        natural_bid: float,
        price_increment: float,
        wait_seconds: float,
        max_attempts: int,
        tick_size: float = 0.01,
    ) -> ChaseResult:
        """
        Execute a soft exit limit chase (canonical SELL).

        Starts at current mid price, moves down by price_increment toward natural_bid.
        Never goes below natural_bid (never becomes a market order).
        """
        limit_price = round_to_tick(current_mid, tick_size)
        floor_price = round_to_tick(natural_bid, tick_size)

        order = LimitOrder(action="SELL", totalQuantity=quantity, lmtPrice=limit_price)
        trade = self.order_service.place_order(
            ib=ib,
            position_id=position_id,
            contract=contract,
            order=order,
            role=ROLE_CLOSE,
            reference_price=current_mid,
        )

        attempt = 1
        self.logger.info(
            f"Soft exit chase started attempt {attempt}/{max_attempts} @ {limit_price} (mid: {current_mid}, natural bid: {floor_price})"
        )

        while attempt <= max_attempts:
            await asyncio.sleep(wait_seconds)

            if trade.orderStatus.status == "Filled":
                return ChaseResult(
                    success=True,
                    trade=trade,
                    filled_quantity=int(trade.orderStatus.filled),
                    avg_fill_price=float(trade.orderStatus.avgFillPrice),
                    status=trade.orderStatus.status,
                    message="Filled completely",
                )

            if trade.orderStatus.status in ("Cancelled", "Inactive"):
                return ChaseResult(
                    success=False,
                    trade=trade,
                    filled_quantity=int(trade.orderStatus.filled),
                    avg_fill_price=float(trade.orderStatus.avgFillPrice) if trade.orderStatus.filled else None,
                    status=trade.orderStatus.status,
                    message=f"Exit order ended in {trade.orderStatus.status}",
                )

            attempt += 1
            if attempt > max_attempts:
                break

            next_limit = round_to_tick(limit_price - price_increment, tick_size)
            if next_limit < floor_price:
                next_limit = floor_price

            if next_limit != limit_price:
                limit_price = next_limit
                self.logger.info(
                    f"Soft exit chase modifying attempt {attempt}/{max_attempts} to limit {limit_price}"
                )
                self.order_service.modify_order(ib, trade, limit_price=limit_price)

        self.logger.warning(
            f"Soft exit chase exhausted {max_attempts} attempts; cancelling order {trade.order.orderRef}"
        )
        self.order_service.cancel_order(ib, trade)
        await self.order_service.wait_for_cancel(trade.order.orderRef, timeout=wait_seconds)

        filled = int(trade.orderStatus.filled)
        return ChaseResult(
            success=filled >= quantity,
            trade=trade,
            filled_quantity=filled,
            avg_fill_price=float(trade.orderStatus.avgFillPrice) if filled else None,
            status=trade.orderStatus.status,
            message=f"Soft exit chase timed out after {max_attempts} attempts",
        )

