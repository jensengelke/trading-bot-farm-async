"""
Broker exit order management.

Creates and places broker-held GTC Take Profit (LMT) and Stop Loss (STP or STP LMT)
orders linked with broker OCA (One-Cancels-All) groups (Plan Section 6B).
"""

import logging
from typing import Optional, Tuple
from ib_async import IB, Contract, LimitOrder, Order, StopLimitOrder, StopOrder, Trade

from bots.optionomega.config import ProfitTargetConfig, StopLossConfig
from bots.optionomega.pricing import calculate_exit_target, round_to_tick
from framework.order_service import OrderService, ROLE_STOP_LOSS, ROLE_TAKE_PROFIT


class BrokerExitOrders:
    """Creates and submits broker-held bracket exit orders through OrderService."""

    def __init__(self, order_service: OrderService, logger: logging.Logger):
        self.order_service = order_service
        self.logger = logger

    def place_protective_exits(
        self,
        ib: IB,
        position_id: str,
        contract: Contract,
        entry_price: float,
        filled_quantity: int,
        profit_target_cfg: Optional[ProfitTargetConfig],
        stop_loss_cfg: Optional[StopLossConfig],
        tick_size: float = 0.01,
    ) -> Tuple[Optional[Trade], Optional[Trade]]:
        """
        Place GTC PT (LMT) and SL (STP / STP LMT) orders linked via broker OCA.

        Closing perspective:
        Opening was canonical BUY. Closing is canonical SELL of the same BAG contract.
        In IB combo conventions, if mark is M, closing SELL limit/stop order prices
        are submitted as M (or broker price convention for combo).
        """
        if filled_quantity <= 0:
            raise ValueError(f"Cannot place protective exits for non-positive quantity {filled_quantity}")

        oca_group = f"OCA_{position_id[:16]}"
        oca_type = 1  # Cancel all remaining orders with block

        pt_trade: Optional[Trade] = None
        sl_trade: Optional[Trade] = None

        # 1. Profit Target
        if profit_target_cfg is not None:
            pt_target_mark = calculate_exit_target(
                entry_price=entry_price,
                target_type=profit_target_cfg.type,
                target_value=profit_target_cfg.value,
                is_profit_target=True,
                tick_size=tick_size,
            )
            pt_order = LimitOrder(
                action="SELL",
                totalQuantity=filled_quantity,
                lmtPrice=pt_target_mark,
                tif="GTC",
            )
            pt_order.ocaGroup = oca_group
            pt_order.ocaType = oca_type
            pt_order.outsideRth = False

            pt_trade = self.order_service.place_order(
                ib=ib,
                position_id=position_id,
                contract=contract,
                order=pt_order,
                role=ROLE_TAKE_PROFIT,
                reference_price=pt_target_mark,
            )
            self.logger.info(
                f"Placed GTC PT limit order {pt_order.orderRef} @ {pt_target_mark} (OCA: {oca_group})"
            )

        # 2. Stop Loss
        if stop_loss_cfg is not None:
            sl_trigger_mark = calculate_exit_target(
                entry_price=entry_price,
                target_type=stop_loss_cfg.type,
                target_value=stop_loss_cfg.value,
                is_profit_target=False,
                tick_size=tick_size,
            )

            if stop_loss_cfg.order_type == "stop_limit":
                offset = stop_loss_cfg.stop_limit_offset or 0.0
                # Selling: limit is trigger - offset (must be <= trigger)
                limit_mark = round_to_tick(sl_trigger_mark - offset, tick_size)
                sl_order = StopLimitOrder(
                    action="SELL",
                    totalQuantity=filled_quantity,
                    lmtPrice=limit_mark,
                    stopPrice=sl_trigger_mark,
                    tif="GTC",
                )
            else:
                sl_order = StopOrder(
                    action="SELL",
                    totalQuantity=filled_quantity,
                    stopPrice=sl_trigger_mark,
                    tif="GTC",
                )

            sl_order.ocaGroup = oca_group
            sl_order.ocaType = oca_type
            sl_order.outsideRth = False

            sl_trade = self.order_service.place_order(
                ib=ib,
                position_id=position_id,
                contract=contract,
                order=sl_order,
                role=ROLE_STOP_LOSS,
                reference_price=sl_trigger_mark,
            )
            self.logger.info(
                f"Placed GTC SL {stop_loss_cfg.order_type} order {sl_order.orderRef} @ stop={sl_trigger_mark} (OCA: {oca_group})"
            )

        return pt_trade, sl_trade
