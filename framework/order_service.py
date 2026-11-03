"""
Order Service

Framework-owned order placement and persistence.

Bots never choose an ``orderRef`` and never write orders, fills or position state
themselves. They ask the framework to place, modify or cancel an order for a virtual
position; the framework

* generates a unique ``orderRef`` ("<bot_id>:<position_id>:<role>"),
* persists the order, every status change, every execution and its commission,
* moves the virtual position through PENDING -> OPEN -> CLOSED (or CANCELLED).

Persistence is driven by IB's global ``orderStatusEvent``, ``execDetailsEvent`` and
``commissionReportEvent``. Attribution is done through the unique ``orderRef``, so
fills of orders placed earlier (e.g. a GTC take profit) are still recorded after a
restart. Events whose ``orderRef`` is unknown to the database (manual TWS orders, other
applications) are ignored.
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, NamedTuple, Optional

from ib_async import IB, Contract, Order, Trade, Fill, CommissionReport
from ib_async.order import OrderStatus
from ib_async.util import UNSET_DOUBLE, dataclassNonDefaults

from framework.position_manager import PositionManager, VirtualPosition
from framework.request_tracker import get_request_tracker


ROLE_OPEN = "open"
ROLE_TAKE_PROFIT = "takeprofit"
ROLE_STOP_LOSS = "stoploss"
ROLE_CLOSE = "close"

VALID_ROLES = (ROLE_OPEN, ROLE_TAKE_PROFIT, ROLE_STOP_LOSS, ROLE_CLOSE)
# Orders that must survive a bot shutdown because they protect an open position
PROTECTIVE_ROLES = (ROLE_TAKE_PROFIT, ROLE_STOP_LOSS)
CLOSING_ROLES = (ROLE_TAKE_PROFIT, ROLE_STOP_LOSS, ROLE_CLOSE)

_REF_SEPARATOR = ":"


class OrderRefParts(NamedTuple):
    bot_id: str
    position_id: str
    role: str


def build_order_ref(bot_id: str, position_id: str, role: str, sequence: int = 1) -> str:
    """Build the unique orderRef. ``sequence`` > 1 is appended when a role is reused."""
    suffix = f"#{sequence}" if sequence > 1 else ""
    return f"{bot_id}{_REF_SEPARATOR}{position_id}{_REF_SEPARATOR}{role}{suffix}"


def parse_order_ref(order_ref: Optional[str]) -> Optional[OrderRefParts]:
    """Parse a framework orderRef; returns None for refs not created by the framework."""
    if not order_ref:
        return None
    parts = order_ref.rsplit(_REF_SEPARATOR, 2)
    if len(parts) != 3:
        return None
    bot_id, position_id, role = parts
    role = role.split("#", 1)[0]
    if not bot_id or not position_id or role not in VALID_ROLES:
        return None
    return OrderRefParts(bot_id, position_id, role)


def is_protective_order_ref(order_ref: Optional[str]) -> bool:
    """True if the orderRef belongs to a take profit or stop loss order."""
    parts = parse_order_ref(order_ref)
    return parts is not None and parts.role in PROTECTIVE_ROLES


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _price_or_none(value: Optional[float]) -> Optional[float]:
    """IB uses a huge sentinel for 'unset' prices; store NULL instead."""
    if value is None or value >= UNSET_DOUBLE:
        return None
    return float(value)


class OrderService:
    """
    Places orders on behalf of bots and persists everything that happens to them.

    One instance exists per database (see :func:`get_order_service`).
    """

    def __init__(self, position_manager: PositionManager, logger: logging.Logger):
        self.position_manager = position_manager
        self.logger = logger
        self._attached_ib: List[IB] = []
        # orderRef -> (status, filled, avgFillPrice) of the last persisted status event
        self._last_status: Dict[str, tuple] = {}
        # IB orderId -> orderRef of the framework orders of this session. IB reports errors
        # by orderId only, so this maps a rejection back to the order (and its bot).
        self._order_refs_by_id: Dict[int, str] = {}
        self._request_tracker = get_request_tracker()
        self._on_protection_rejected_listeners: List[Callable] = []
        self._on_partial_fill_listeners: List[Callable] = []

    def add_protection_rejected_listener(self, listener: Callable) -> None:
        """Register a callback for protective order rejections: callback(position_id, order_ref, error_code, error_message)."""
        self._on_protection_rejected_listeners.append(listener)

    def remove_protection_rejected_listener(self, listener: Callable) -> None:
        if listener in self._on_protection_rejected_listeners:
            self._on_protection_rejected_listeners.remove(listener)

    def add_partial_fill_listener(self, listener: Callable) -> None:
        """Register a callback for partial fills: callback(position_id, order_ref, filled_qty, avg_price)."""
        self._on_partial_fill_listeners.append(listener)

    def remove_partial_fill_listener(self, listener: Callable) -> None:
        if listener in self._on_partial_fill_listeners:
            self._on_partial_fill_listeners.remove(listener)


    # ------------------------------------------------------------------
    # Event wiring
    # ------------------------------------------------------------------

    def attach(self, ib: IB) -> None:
        """
        Subscribe to the IB events used for persistence and persist everything the
        session already knows about. Safe to call repeatedly.
        """
        if any(ib is attached for attached in self._attached_ib):
            return
        ib.orderStatusEvent += self._on_order_status
        ib.execDetailsEvent += self._on_exec_details
        ib.commissionReportEvent += self._on_commission_report
        ib.errorEvent += self._on_error
        self._attached_ib.append(ib)
        self.logger.info("Order persistence attached to IB connection")
        self.sync_from_ib(ib)

    def detach(self, ib: IB) -> None:
        """Unsubscribe from the IB events."""
        for attached in list(self._attached_ib):
            if attached is ib:
                ib.orderStatusEvent -= self._on_order_status
                ib.execDetailsEvent -= self._on_exec_details
                ib.commissionReportEvent -= self._on_commission_report
                ib.errorEvent -= self._on_error
                self._attached_ib.remove(attached)

    def sync_from_ib(self, ib: IB) -> None:
        """
        Persist status and executions that IB reported before the handlers were attached
        (orders and executions replayed on connect are not delivered as live events).
        """
        for trade in ib.trades():
            parts = parse_order_ref(trade.order.orderRef)
            if parts is None:
                continue
            self._track_order(trade.order.orderId, trade.order.orderRef, parts.bot_id)
            self._persist_status(trade)
            for fill in trade.fills:
                self._persist_fill(trade, fill)

    # ------------------------------------------------------------------
    # Positions
    # ------------------------------------------------------------------

    def create_position(
        self,
        bot_id: str,
        bot_config: Dict[str, Any],
        underlying_symbol: str,
        *args: Any,
        legs: Optional[List[Dict[str, Any]]] = None,
        quantity: int = 1,
        multiplier: float = 100.0,
        close_before_first_expiry: bool = False,
        first_expiry_cutoff: Optional[str] = None,
        soft_exit_state: Optional[str] = None,
        expiration: Optional[str] = None,
        legs_data: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> VirtualPosition:
        """
        Create and persist an ENTRY_PENDING virtual position owned by ``bot_id``.

        ``bot_config`` is stored once as a bot version (bots table); the position only
        references that version.
        """
        if args:
            if isinstance(args[0], (list, tuple)):
                legs = list(args[0])
                if len(args) > 1 and isinstance(args[1], (int, float)):
                    quantity = int(args[1])
            elif isinstance(args[0], str):
                expiration = args[0]
                if len(args) > 1:
                    if isinstance(args[1], dict):
                        legs_data = args[1]
                    elif isinstance(args[1], list):
                        legs = args[1]
                if len(args) > 2 and isinstance(args[2], (int, float)):
                    quantity = int(args[2])

        if legs is None and legs_data is not None:
            legs = legs_data.get("legs", [])
        position = VirtualPosition(
            position_id=uuid.uuid4().hex,
            bot_id=bot_id,
            status="ENTRY_PENDING",
            underlying_symbol=underlying_symbol,
            quantity=quantity,
            opening_order_ref="",
            multiplier=multiplier,
            close_before_first_expiry=close_before_first_expiry,
            first_expiry_cutoff=first_expiry_cutoff,
            soft_exit_state=soft_exit_state,
            bot_version_id=self.position_manager.register_bot_version(bot_id, bot_config),
            legs=legs or [],
            expiration=expiration,
            **kwargs,
        )
        if not self.position_manager.create_position(position, legs=legs):
            raise RuntimeError(f"Could not persist virtual position for bot {bot_id}")
        return position


    def _set_position_status(self, position_id: str, status: str, **changes: Any) -> None:
        position = self.position_manager.get_position(position_id)
        if position is None:
            return
        position.status = status
        for name, value in changes.items():
            setattr(position, name, value)
        self.position_manager.update_position(position)
        self.logger.info(f"Virtual position {position_id} is now {status}")

    # ------------------------------------------------------------------
    # Order placement, modification, cancellation
    # ------------------------------------------------------------------

    def place_order(
        self, ib: IB, position_id: str, contract: Contract, order: Order, role: str,
        reference_price: Optional[float] = None
    ) -> Trade:
        """
        Place an order for a virtual position.

        Any ``orderRef`` already set on ``order`` is replaced. The order row is written
        before the order is sent so no IB event can arrive for an unknown order.
        """
        if role not in VALID_ROLES:
            raise ValueError(f"Unknown order role '{role}', expected one of {VALID_ROLES}")
        position = self.position_manager.get_position(position_id)
        if position is None:
            raise ValueError(f"Unknown virtual position {position_id}")
        self.attach(ib)

        if order.orderRef:
            self.logger.warning(
                f"Ignoring caller-provided orderRef '{order.orderRef}'; the framework assigns it"
            )
        sequence = 1
        order_ref = build_order_ref(position.bot_id, position_id, role, sequence)
        while self.position_manager.get_order(order_ref) is not None:
            sequence += 1
            order_ref = build_order_ref(position.bot_id, position_id, role, sequence)
        order.orderRef = order_ref

        stored = self.position_manager.record_order({
            "order_ref": order_ref,
            "position_id": position_id,
            "bot_id": position.bot_id,
            "role": role,
            "action": order.action,
            "order_type": order.orderType,
            "tif": order.tif or None,
            "total_quantity": float(order.totalQuantity),
            "limit_price": _price_or_none(order.lmtPrice),
            "aux_price": _price_or_none(order.auxPrice),
            "contract_json": self._contract_json(contract),
            "status": "PendingSubmit",
            "client_id": ib.client.clientId if hasattr(ib, "client") and isinstance(getattr(ib.client, "clientId", None), int) else None,
            "account": order.account or None,
            "oca_group": order.ocaGroup or None,
            "oca_type": order.ocaType if order.ocaGroup else None,
            "trigger_method": order.triggerMethod or None,
            "outside_rth": 1 if order.outsideRth else 0,
            "reference_price": reference_price,
        })
        if not stored:
            raise RuntimeError(f"Could not persist order {order_ref}; order not sent")

        self.position_manager.record_order_event({
            "order_ref": order_ref,
            "position_id": position_id,
            "bot_id": position.bot_id,
            "event_type": "placed",
            "status": "PendingSubmit",
            "filled_quantity": 0,
            "remaining_quantity": float(order.totalQuantity),
            "limit_price": _price_or_none(order.lmtPrice),
            "aux_price": _price_or_none(order.auxPrice),
        })

        try:
            trade = ib.placeOrder(contract, order)
        except Exception:
            self.position_manager.update_order(order_ref, status="Error")
            if role == ROLE_OPEN:
                self._set_position_status(position_id, "CANCELLED")
            raise

        self._track_order(order.orderId, order_ref, position.bot_id)
        self.position_manager.update_order(order_ref, order_id=order.orderId)
        self._link_order_to_position(position_id, role, order_ref, order.orderId)
        return trade


    def _track_order(self, order_id: int, order_ref: str, bot_id: str) -> None:
        """
        Register the IB orderId with the request tracker so IB errors for this order are
        dispatched to the bot that placed it, and remember it for persisting rejections.
        """
        if not order_id:
            return
        self._order_refs_by_id[order_id] = order_ref
        self._request_tracker.register_request(order_id, bot_id)

    def modify_order(
        self, ib: IB, trade: Trade,
        limit_price: Optional[float] = None, aux_price: Optional[float] = None
    ) -> Trade:
        """Change the limit and/or stop price of a working framework order."""
        order_ref = trade.order.orderRef
        if parse_order_ref(order_ref) is None:
            raise ValueError(f"Order '{order_ref}' was not placed through the framework")
        stored = self.position_manager.get_order(order_ref)
        if stored and stored.get("status") in OrderStatus.DoneStates:
            raise ValueError(f"Cannot modify terminal order {order_ref} with status {stored['status']}")
        if limit_price is not None:
            trade.order.lmtPrice = limit_price
        if aux_price is not None:
            trade.order.auxPrice = aux_price
        modified = ib.placeOrder(trade.contract, trade.order)
        self.position_manager.record_order_modification(
            order_ref,
            _price_or_none(trade.order.lmtPrice),
            _price_or_none(trade.order.auxPrice),
        )
        if stored:
            self.position_manager.record_order_event({
                "order_ref": order_ref,
                "position_id": stored["position_id"],
                "bot_id": stored["bot_id"],
                "event_type": "modified",
                "status": stored.get("status"),
                "limit_price": _price_or_none(trade.order.lmtPrice),
                "aux_price": _price_or_none(trade.order.auxPrice),
            })
        return modified

    def cancel_order(self, ib: IB, trade: Trade) -> None:
        """Cancel a framework order. The final status is persisted from IB's status event."""
        order_ref = trade.order.orderRef
        stored = self._known_order(order_ref)
        if stored:
            self.position_manager.record_order_event({
                "order_ref": order_ref,
                "position_id": stored["position_id"],
                "bot_id": stored["bot_id"],
                "event_type": "cancel_requested",
                "status": stored.get("status"),
            })
        ib.cancelOrder(trade.order)

    async def wait_for_cancel(
        self, order_ref: str, timeout: float = 10.0, poll_interval: float = 0.1
    ) -> bool:
        """Wait until order cancellation reaches a terminal status or timeout."""
        start = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start < timeout:
            stored = self.position_manager.get_order(order_ref)
            if stored and stored.get("status") in OrderStatus.DoneStates:
                return True
            await asyncio.sleep(poll_interval)
        return False


    def cancel_unprotected_orders(self, ib: IB, bot_id: str) -> List[str]:
        """
        Cancel the working orders of ``bot_id`` that are not take profit / stop loss
        orders (e.g. an opening order still being chased). Used on bot shutdown.

        Returns:
            The orderRefs that were cancelled
        """
        cancelled: List[str] = []
        for trade in ib.openTrades():
            parts = parse_order_ref(trade.order.orderRef)
            if parts is None or parts.bot_id != bot_id:
                continue
            if parts.role in PROTECTIVE_ROLES:
                self.logger.info(f"Preserving protective order {trade.order.orderRef}")
                continue
            ib.cancelOrder(trade.order)
            cancelled.append(trade.order.orderRef)
            self.logger.info(f"Cancelled order {trade.order.orderRef}")
        return cancelled

    def _link_order_to_position(
        self, position_id: str, role: str, order_ref: str, order_id: int
    ) -> None:
        position = self.position_manager.get_position(position_id)
        if position is None:
            return
        if role == ROLE_OPEN:
            position.opening_order_ref = order_ref
        elif role == ROLE_TAKE_PROFIT:
            position.tp_order_id = order_id
        elif role == ROLE_STOP_LOSS:
            position.sl_order_id = order_id
        else:
            return
        self.position_manager.update_position(position)

    @staticmethod
    def _contract_json(contract: Contract) -> str:
        data = dataclassNonDefaults(contract)
        legs = data.get("comboLegs")
        if legs:
            data["comboLegs"] = [dataclassNonDefaults(leg) for leg in legs]
        return json.dumps(data, default=str)

    # ------------------------------------------------------------------
    # IB event handlers
    # ------------------------------------------------------------------

    def _on_order_status(self, trade: Trade) -> None:
        try:
            self._persist_status(trade)
        except Exception as e:
            self.logger.error(f"Error persisting order status: {e}", exc_info=True)

    def _on_exec_details(self, trade: Trade, fill: Fill) -> None:
        try:
            self._persist_fill(trade, fill)
        except Exception as e:
            self.logger.error(f"Error persisting execution: {e}", exc_info=True)

    def _on_commission_report(self, trade: Trade, fill: Fill, report: CommissionReport) -> None:
        try:
            self._persist_commission(report)
        except Exception as e:
            self.logger.error(f"Error persisting commission: {e}", exc_info=True)

    def _on_error(self, req_id: int, error_code: int, error_string: str, contract: Any) -> None:
        """Persist why IB rejected, cancelled or warned about a framework order."""
        order_ref = self._order_refs_by_id.get(req_id)
        if order_ref is None:
            return
        try:
            self.position_manager.update_order(
                order_ref, status_message=error_string, error_code=error_code
            )
            self.logger.info(f"Order {order_ref}: IB error {error_code}: {error_string}")

            stored = self.position_manager.get_order(order_ref)
            if stored:
                self.position_manager.record_order_event({
                    "order_ref": order_ref,
                    "position_id": stored["position_id"],
                    "bot_id": stored["bot_id"],
                    "event_type": "error",
                    "status": stored.get("status"),
                    "error_code": error_code,
                    "message": error_string,
                })

            parts = parse_order_ref(order_ref)
            if parts and parts.role in PROTECTIVE_ROLES:
                pos_id = stored["position_id"] if stored else parts.position_id
                for listener in self._on_protection_rejected_listeners:
                    try:
                        listener(pos_id, order_ref, error_code, error_string)
                    except Exception as e:
                        self.logger.error(f"Error in protection rejected listener: {e}", exc_info=True)
        except Exception as e:
            self.logger.error(f"Error persisting order error: {e}", exc_info=True)

    def _known_order(self, order_ref: Optional[str]) -> Optional[Dict[str, Any]]:
        """Return the stored order for a framework orderRef, or None for foreign orders."""
        if parse_order_ref(order_ref) is None:
            return None
        return self.position_manager.get_order(order_ref)

    def _persist_status(self, trade: Trade) -> None:
        order_ref = trade.order.orderRef
        stored = self._known_order(order_ref)
        if stored is None:
            return
        status = trade.orderStatus
        signature = (status.status, status.filled, status.avgFillPrice, status.permId)
        if self._last_status.get(order_ref) == signature:
            return
        self._last_status[order_ref] = signature

        fields: Dict[str, Any] = {
            "status": status.status,
            "filled_quantity": float(status.filled),
            "order_id": trade.order.orderId or stored["order_id"],
            "perm_id": status.permId or trade.order.permId or stored["perm_id"],
        }
        if status.filled:
            fields["avg_fill_price"] = float(status.avgFillPrice)
        self.position_manager.update_order(order_ref, **fields)

        self.position_manager.record_order_event({
            "order_ref": order_ref,
            "position_id": stored["position_id"],
            "bot_id": stored["bot_id"],
            "event_type": "status",
            "status": status.status,
            "filled_quantity": float(status.filled),
            "remaining_quantity": float(status.remaining),
            "avg_fill_price": _price_or_none(status.avgFillPrice),
        })

        # Partial fill notification
        if 0 < status.filled < trade.order.totalQuantity and status.status not in OrderStatus.DoneStates:
            for listener in self._on_partial_fill_listeners:
                try:
                    listener(stored["position_id"], order_ref, float(status.filled), float(status.avgFillPrice))
                except Exception as e:
                    self.logger.error(f"Error in partial fill listener: {e}", exc_info=True)

        if status.status in OrderStatus.DoneStates:
            self._apply_final_status(trade, stored, status)

    def _apply_final_status(self, trade: Trade, stored: Dict[str, Any], status: OrderStatus) -> None:
        """Move the virtual position forward once one of its orders reached a final state."""
        position = self.position_manager.get_position(stored["position_id"])
        if position is None:
            return
        role = stored["role"]
        filled = float(status.filled)
        when = self._last_fill_time(trade) or _utc_now()

        if role == ROLE_OPEN and position.status in ("ENTRY_PENDING", "PENDING"):
            if filled > 0:
                position.status = "OPEN"
                position.quantity = int(filled)
                position.signed_entry_price = float(status.avgFillPrice)
                position.opening_fill_time = when
            else:
                position.status = "CANCELLED"
            self.position_manager.update_position(position)
            self.logger.info(f"Virtual position {position.position_id} is now {position.status}")
        elif role in CLOSING_ROLES and position.status in ("OPEN", "EXIT_WORKING", "EXIT_CANCEL_PENDING") and status.status == "Filled":
            position.status = "CLOSED"
            position.closing_fill_price = float(status.avgFillPrice)
            position.closing_fill_time = when
            self.position_manager.update_position(position)
            self.logger.info(
                f"Virtual position {position.position_id} is now CLOSED by {stored['order_ref']}"
            )
            self._cancel_remaining_orders(position.position_id, stored["order_ref"])
        elif role in CLOSING_ROLES and position.status == "CLOSED" and status.status == "Filled":
            position.status = "NEEDS_RECONCILIATION"
            self.position_manager.update_position(position)
            self.logger.critical(
                f"Virtual position {position.position_id} experienced racing second fill; set to NEEDS_RECONCILIATION"
            )


    def _cancel_remaining_orders(self, position_id: str, except_order_ref: str) -> None:
        """A closed position must not keep a live stop loss / take profit (no OCA group)."""
        for ib in self._attached_ib:
            for trade in ib.openTrades():
                parts = parse_order_ref(trade.order.orderRef)
                if parts is None or parts.position_id != position_id:
                    continue
                if trade.order.orderRef == except_order_ref:
                    continue
                self.logger.info(f"Cancelling {trade.order.orderRef}: position {position_id} is closed")
                ib.cancelOrder(trade.order)

    @staticmethod
    def _last_fill_time(trade: Trade) -> Optional[str]:
        times = [f.time for f in trade.fills if f.time is not None]
        return max(times).isoformat() if times else None

    def _persist_fill(self, trade: Trade, fill: Fill) -> None:
        execution = fill.execution
        order_ref = trade.order.orderRef or execution.orderRef
        stored = self._known_order(order_ref)
        if stored is None:
            return
        contract = fill.contract
        report = fill.commissionReport
        has_commission = report is not None and report.execId == execution.execId
        self.position_manager.record_fill({
            "exec_id": execution.execId,
            "order_ref": order_ref,
            "position_id": stored["position_id"],
            "bot_id": stored["bot_id"],
            "perm_id": execution.permId or None,
            "order_id": execution.orderId,
            "time": fill.time.isoformat(),
            "con_id": contract.conId or None,
            "sec_type": contract.secType or None,
            "symbol": contract.symbol or None,
            "local_symbol": contract.localSymbol or None,
            "side": execution.side,
            "shares": float(execution.shares),
            "price": float(execution.price),
            "cum_qty": float(execution.cumQty),
            "avg_price": float(execution.avgPrice),
            "exchange": execution.exchange or None,
            "account": execution.acctNumber or None,
            "commission": _price_or_none(report.commission) if has_commission else None,
            "commission_currency": (report.currency or None) if has_commission else None,
            "realized_pnl": _price_or_none(report.realizedPNL) if has_commission else None,
        })

    def _persist_commission(self, report: CommissionReport) -> None:
        self.position_manager.update_fill_commission(
            report.execId,
            _price_or_none(report.commission),
            report.currency or None,
            _price_or_none(report.realizedPNL),
        )


# One OrderService per database file, shared by all bots of an instance
_services: Dict[str, OrderService] = {}


def get_order_service(db_path: str, logger: Optional[logging.Logger] = None) -> OrderService:
    """Get (or create) the OrderService for the SQLite database at ``db_path``."""
    key = str(Path(db_path).resolve())
    service = _services.get(key)
    if service is None:
        if logger is None:
            try:
                from framework.logging_config import get_system_logger
                logger = get_system_logger()
            except RuntimeError:
                logger = logging.getLogger("system")
        service = OrderService(PositionManager(db_path, logger), logger)
        _services[key] = service
    return service
