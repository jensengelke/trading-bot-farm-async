"""
Unit tests for the framework order persistence (OrderService / PositionManager).

IB is simulated: a real ``IB`` object is used only for its events, and ``placeOrder`` /
``cancelOrder`` are mocked, so no gateway is needed.
"""

import json
import logging
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from ib_async import (
    IB, Contract, ComboLeg, Trade, OrderStatus, Fill, Execution, CommissionReport
)
from ib_async.order import LimitOrder, StopOrder

from framework.order_service import (
    OrderService, ROLE_OPEN, ROLE_TAKE_PROFIT, ROLE_STOP_LOSS,
    build_order_ref, parse_order_ref, is_protective_order_ref,
)
from framework.db_schema import SCHEMA_VERSION, config_version_id
from framework.position_manager import PositionManager
from framework.request_tracker import get_request_tracker


def make_combo() -> Contract:
    combo = Contract(secType="BAG", symbol="SPX", currency="USD", exchange="SMART")
    combo.comboLegs = [
        ComboLeg(conId=1, ratio=1, action="SELL", exchange="SMART"),
        ComboLeg(conId=2, ratio=1, action="BUY", exchange="SMART"),
    ]
    return combo


class OrderServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test_positions.db")
        logger = logging.getLogger("test_order_service")
        self.pm = PositionManager(self.db_path, logger)
        self.service = OrderService(self.pm, logger)
        self.ib = IB()
        self.next_order_id = 100

        def fake_place_order(contract, order):
            if not order.orderId:
                order.orderId = self.next_order_id
                self.next_order_id += 1
            trade = self.ib.wrapper.trades.get(order.orderId)
            if trade is None:
                trade = Trade(contract, order, OrderStatus(orderId=order.orderId, status="PendingSubmit"))
                self.ib.wrapper.trades[order.orderId] = trade
            return trade

        self.ib.placeOrder = MagicMock(side_effect=fake_place_order)
        self.ib.cancelOrder = MagicMock()
        self.service.attach(self.ib)

    def tearDown(self):
        self.tmp.cleanup()

    def new_position(self, bot_id="strategy_fkk"):
        return self.service.create_position(
            bot_id=bot_id,
            bot_config={"type": "strategy", "dte": 0},
            underlying_symbol="SPX",
            expiration="20260702",
            legs_data={"legs": []},
            quantity=1,
        )

    def place(self, position, order, role):
        return self.service.place_order(self.ib, position.position_id, make_combo(), order, role)

    def set_status(self, trade, status, filled=0.0, avg=0.0):
        trade.orderStatus.status = status
        trade.orderStatus.filled = filled
        trade.orderStatus.avgFillPrice = avg
        trade.orderStatus.permId = trade.order.orderId + 5000
        self.ib.orderStatusEvent.emit(trade)

    def execute(self, trade, exec_id, shares, price, side="BOT"):
        execution = Execution(
            execId=exec_id, side=side, shares=shares, price=price, cumQty=shares,
            avgPrice=price, orderId=trade.order.orderId, permId=trade.order.orderId + 5000,
            orderRef=trade.order.orderRef,
        )
        fill = Fill(Contract(conId=0, secType="BAG", symbol="SPX"), execution, CommissionReport(),
                    datetime(2026, 7, 2, 17, 0, tzinfo=timezone.utc))
        trade.fills.append(fill)
        self.ib.execDetailsEvent.emit(trade, fill)
        return fill


class TestOrderRef(unittest.TestCase):
    def test_round_trip(self):
        ref = build_order_ref("strategy_fkk", "abc123", "takeprofit")
        self.assertEqual(ref, "strategy_fkk:abc123:takeprofit")
        parts = parse_order_ref(ref)
        self.assertEqual((parts.bot_id, parts.position_id, parts.role),
                         ("strategy_fkk", "abc123", "takeprofit"))

    def test_sequence_suffix(self):
        ref = build_order_ref("b", "p", "close", 2)
        self.assertEqual(ref, "b:p:close#2")
        self.assertEqual(parse_order_ref(ref).role, "close")

    def test_foreign_refs_are_ignored(self):
        for ref in (None, "", "strategy_fkk_strategy", "a:b:unknown", "only:two"):
            self.assertIsNone(parse_order_ref(ref))

    def test_protective(self):
        self.assertTrue(is_protective_order_ref("b:p:stoploss"))
        self.assertTrue(is_protective_order_ref("b:p:takeprofit"))
        self.assertFalse(is_protective_order_ref("b:p:open"))

class TestOrderPlacement(OrderServiceTestCase):
    def test_framework_assigns_unique_order_ref_and_overrides_caller(self):
        position = self.new_position()
        order = LimitOrder("BUY", 1, -1.3)
        order.orderRef = "bot-chosen-ref"
        trade = self.place(position, order, ROLE_OPEN)

        self.assertEqual(trade.order.orderRef, f"strategy_fkk:{position.position_id}:open")
        stored = self.pm.get_order(trade.order.orderRef)
        self.assertEqual(stored["role"], "open")
        self.assertEqual(stored["bot_id"], "strategy_fkk")
        self.assertEqual(stored["limit_price"], -1.3)
        self.assertIsNone(stored["aux_price"])  # IB's unset sentinel is stored as NULL
        self.assertEqual(self.pm.get_position(position.position_id).opening_order_ref,
                         trade.order.orderRef)

    def test_two_positions_of_same_bot_get_distinct_refs(self):
        t1 = self.place(self.new_position(), LimitOrder("BUY", 1, 1.0), ROLE_OPEN)
        t2 = self.place(self.new_position(), LimitOrder("BUY", 1, 1.0), ROLE_OPEN)
        self.assertNotEqual(t1.order.orderRef, t2.order.orderRef)

    def test_repeated_role_gets_sequence_suffix(self):
        p = self.new_position()
        t1 = self.place(p, LimitOrder("SELL", 1, 2.0), ROLE_TAKE_PROFIT)
        t2 = self.place(p, LimitOrder("SELL", 1, 2.5), ROLE_TAKE_PROFIT)
        self.assertTrue(t2.order.orderRef.endswith(":takeprofit#2"))
        self.assertNotEqual(t1.order.orderRef, t2.order.orderRef)

    def test_unknown_position_and_role_are_rejected(self):
        with self.assertRaises(ValueError):
            self.service.place_order(self.ib, "missing", make_combo(), LimitOrder("BUY", 1, 1.0), ROLE_OPEN)
        with self.assertRaises(ValueError):
            self.place(self.new_position(), LimitOrder("BUY", 1, 1.0), "bogus")
        self.ib.placeOrder.assert_not_called()

    def test_modify_counts_and_stores_price(self):
        trade = self.place(self.new_position(), LimitOrder("BUY", 1, -1.3), ROLE_OPEN)
        self.service.modify_order(self.ib, trade, limit_price=-1.25)
        self.service.modify_order(self.ib, trade, limit_price=-1.2)
        stored = self.pm.get_order(trade.order.orderRef)
        self.assertEqual(stored["modify_count"], 2)
        self.assertEqual(stored["limit_price"], -1.2)

    def test_modify_rejects_orders_not_placed_by_framework(self):
        foreign = Trade(make_combo(), LimitOrder("BUY", 1, 1.0), OrderStatus())
        foreign.order.orderRef = "manual"
        with self.assertRaises(ValueError):
            self.service.modify_order(self.ib, foreign, limit_price=1.1)

    def test_place_failure_cancels_pending_position(self):
        p = self.new_position()
        self.ib.placeOrder.side_effect = RuntimeError("socket gone")
        with self.assertRaises(RuntimeError):
            self.place(p, LimitOrder("BUY", 1, 1.0), ROLE_OPEN)
        self.assertEqual(self.pm.get_position(p.position_id).status, "CANCELLED")
        self.assertEqual(self.pm.get_orders_for_position(p.position_id)[0]["status"], "Error")


class TestPersistenceFromEvents(OrderServiceTestCase):
    def test_open_fill_opens_position(self):
        p = self.new_position()
        trade = self.place(p, LimitOrder("BUY", 1, -1.3), ROLE_OPEN)
        self.set_status(trade, "Submitted")
        self.execute(trade, "E1", 1, -1.3)
        self.set_status(trade, "Filled", filled=1, avg=-1.3)

        position = self.pm.get_position(p.position_id)
        self.assertEqual(position.status, "OPEN")
        self.assertEqual(position.opening_fill_price, -1.3)
        self.assertEqual(position.opening_fill_time, "2026-07-02T17:00:00+00:00")
        version = self.pm.get_bot_version("strategy_fkk", position.bot_version_id)
        self.assertEqual(version["config"], {"type": "strategy", "dte": 0})
        stored = self.pm.get_order(trade.order.orderRef)
        self.assertEqual((stored["status"], stored["filled_quantity"], stored["avg_fill_price"]),
                         ("Filled", 1.0, -1.3))
        self.assertEqual(stored["perm_id"], trade.order.orderId + 5000)
        fills = self.pm.get_fills_for_position(p.position_id)
        self.assertEqual([f["exec_id"] for f in fills], ["E1"])
        self.assertEqual(fills[0]["bot_id"], "strategy_fkk")

    def test_cancelled_open_order_cancels_position(self):
        p = self.new_position()
        trade = self.place(p, LimitOrder("BUY", 1, -1.3), ROLE_OPEN)
        self.set_status(trade, "Cancelled")
        self.assertEqual(self.pm.get_position(p.position_id).status, "CANCELLED")

    def test_take_profit_fill_closes_position_and_cancels_stop(self):
        p = self.new_position()
        open_trade = self.place(p, LimitOrder("BUY", 1, -1.3), ROLE_OPEN)
        self.set_status(open_trade, "Filled", filled=1, avg=-1.3)
        tp = self.place(p, LimitOrder("SELL", 1, -0.3), ROLE_TAKE_PROFIT)
        sl = self.place(p, StopOrder("SELL", 1, -2.6), ROLE_STOP_LOSS)
        self.set_status(tp, "Submitted")
        self.set_status(sl, "Submitted")

        position = self.pm.get_position(p.position_id)
        self.assertEqual(position.tp_order_id, tp.order.orderId)
        self.assertEqual(position.sl_order_id, sl.order.orderId)

        self.execute(tp, "E2", 1, -0.3, side="SLD")
        self.set_status(tp, "Filled", filled=1, avg=-0.3)

        position = self.pm.get_position(p.position_id)
        self.assertEqual(position.status, "CLOSED")
        self.assertEqual(position.closing_fill_price, -0.3)
        self.ib.cancelOrder.assert_called_once_with(sl.order)

    def test_commission_report_updates_fill(self):
        p = self.new_position()
        trade = self.place(p, LimitOrder("BUY", 1, 1.0), ROLE_OPEN)
        fill = self.execute(trade, "E1", 1, 1.0)
        report = CommissionReport(execId="E1", commission=2.5, currency="USD", realizedPNL=0.0)
        self.ib.commissionReportEvent.emit(trade, fill, report)
        stored = self.pm.get_fills_for_position(p.position_id)[0]
        self.assertEqual((stored["commission"], stored["commission_currency"]), (2.5, "USD"))

    def test_duplicate_execution_is_stored_once(self):
        p = self.new_position()
        trade = self.place(p, LimitOrder("BUY", 1, 1.0), ROLE_OPEN)
        fill = self.execute(trade, "E1", 1, 1.0)
        self.ib.execDetailsEvent.emit(trade, fill)
        self.assertEqual(len(self.pm.get_fills_for_position(p.position_id)), 1)

    def test_events_for_foreign_orders_are_ignored(self):
        manual = Trade(make_combo(), LimitOrder("BUY", 1, 1.0), OrderStatus(orderId=7, status="Filled"))
        manual.order.orderRef = "typed-into-tws"
        self.ib.orderStatusEvent.emit(manual)
        self.execute(manual, "EX", 1, 1.0)
        conn = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 0)
        finally:
            conn.close()

    def test_framework_ref_missing_from_database_is_ignored(self):
        ghost = Trade(make_combo(), LimitOrder("BUY", 1, 1.0), OrderStatus(orderId=8, status="Filled"))
        ghost.order.orderRef = "strategy_fkk:nosuchposition:open"
        self.ib.orderStatusEvent.emit(ghost)
        self.execute(ghost, "EG", 1, 1.0)
        self.assertEqual(self.pm.get_fills_for_position("nosuchposition"), [])

    def test_sync_from_ib_persists_what_happened_before_attach(self):
        p = self.new_position()
        trade = self.place(p, LimitOrder("BUY", 1, 1.0), ROLE_OPEN)
        self.service.detach(self.ib)
        self.execute(trade, "E9", 1, 1.0)
        self.set_status(trade, "Filled", filled=1, avg=1.0)
        self.assertEqual(self.pm.get_position(p.position_id).status, "ENTRY_PENDING")

        self.service.attach(self.ib)

        self.assertEqual(self.pm.get_position(p.position_id).status, "OPEN")
        self.assertEqual(len(self.pm.get_fills_for_position(p.position_id)), 1)


class TestShutdown(OrderServiceTestCase):
    def test_cancels_only_unprotected_orders_of_the_bot(self):
        p = self.new_position()
        other = self.new_position(bot_id="strategy_other")
        opening = self.place(p, LimitOrder("BUY", 1, 1.0), ROLE_OPEN)
        tp = self.place(p, LimitOrder("SELL", 1, 2.0), ROLE_TAKE_PROFIT)
        sl = self.place(p, StopOrder("SELL", 1, 0.5), ROLE_STOP_LOSS)
        foreign_open = self.place(other, LimitOrder("BUY", 1, 1.0), ROLE_OPEN)
        for trade in (opening, tp, sl, foreign_open):
            self.set_status(trade, "Submitted")

        cancelled = self.service.cancel_unprotected_orders(self.ib, "strategy_fkk")

        self.assertEqual(cancelled, [opening.order.orderRef])
        self.ib.cancelOrder.assert_called_once_with(opening.order)

class TestBotVersions(OrderServiceTestCase):
    def test_positions_share_one_version_row_and_config_is_not_copied(self):
        p1, p2 = self.new_position(), self.new_position()
        self.assertEqual(p1.bot_version_id, p2.bot_version_id)
        versions = self.pm.get_bot_versions("strategy_fkk")
        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0]["version_id"], p1.bot_version_id)
        conn = sqlite3.connect(self.db_path)
        try:
            columns = {r[1] for r in conn.execute("PRAGMA table_info(virtual_positions)")}
        finally:
            conn.close()
        self.assertNotIn("bot_config", columns)

    def test_changed_config_creates_new_version_and_old_one_keeps_its_date(self):
        old = self.service.create_position("strategy_fkk", {"dte": 0}, "SPX", "20260702", {}, 1)
        new = self.service.create_position("strategy_fkk", {"dte": 7}, "SPX", "20260702", {}, 1)
        self.assertNotEqual(old.bot_version_id, new.bot_version_id)
        first_valid_from = self.pm.get_bot_version("strategy_fkk", old.bot_version_id)["valid_from"]
        again = self.service.create_position("strategy_fkk", {"dte": 0}, "SPX", "20260702", {}, 1)
        self.assertEqual(again.bot_version_id, old.bot_version_id)
        self.assertEqual(len(self.pm.get_bot_versions("strategy_fkk")), 2)
        self.assertEqual(
            self.pm.get_bot_version("strategy_fkk", old.bot_version_id)["valid_from"],
            first_valid_from)

    def test_version_id_ignores_key_order(self):
        self.assertEqual(config_version_id({"a": 1, "b": {"c": 2, "d": 3}}),
                         config_version_id({"b": {"d": 3, "c": 2}, "a": 1}))

    def test_same_config_of_different_bots_is_separate(self):
        a = self.new_position("strategy_a")
        b = self.new_position("strategy_b")
        self.assertEqual(a.bot_version_id, b.bot_version_id)  # same hash ...
        self.assertEqual(len(self.pm.get_bot_versions("strategy_a")), 1)  # ... one row per bot
        self.assertEqual(len(self.pm.get_bot_versions("strategy_b")), 1)

    def test_position_requires_existing_version(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO virtual_positions (position_id, bot_id, bot_version_id, status,"
                    " underlying_symbol, quantity, opening_order_ref,"
                    " created_at, updated_at) VALUES ('x','strategy_fkk','nope','ENTRY_PENDING','SPX',"
                    "1,'','t','t')")
        finally:
            conn.close()


class TestOrderErrors(OrderServiceTestCase):
    def test_rejection_reason_is_persisted(self):
        p = self.new_position()
        trade = self.place(p, LimitOrder("BUY", 1, -3.5), ROLE_OPEN)
        self.ib.errorEvent.emit(trade.order.orderId, 201,
                                "Order rejected - reason: Available Funds are insufficient", None)
        self.set_status(trade, "Inactive")
        stored = self.pm.get_order(trade.order.orderRef)
        self.assertEqual(stored["error_code"], 201)
        self.assertIn("Available Funds", stored["status_message"])
        self.assertEqual(stored["status"], "Inactive")
        self.assertEqual(self.pm.get_position(p.position_id).status, "CANCELLED")

    def test_errors_of_other_requests_are_ignored(self):
        p = self.new_position()
        trade = self.place(p, LimitOrder("BUY", 1, 1.0), ROLE_OPEN)
        self.ib.errorEvent.emit(-1, 2104, "Market data farm connection is OK", None)
        self.ib.errorEvent.emit(trade.order.orderId + 999, 200, "No security definition", None)
        self.assertIsNone(self.pm.get_order(trade.order.orderRef)["error_code"])

    def test_order_id_is_registered_for_error_routing_to_the_bot(self):
        p = self.new_position("strategy_routed")
        trade = self.place(p, LimitOrder("BUY", 1, 1.0), ROLE_OPEN)
        self.assertEqual(
            get_request_tracker().get_bot_for_request(trade.order.orderId), "strategy_routed")

    def test_sync_registers_orders_that_survived_a_restart(self):
        p = self.new_position("strategy_restarted")
        trade = self.place(p, LimitOrder("SELL", 1, 1.0), ROLE_TAKE_PROFIT)
        get_request_tracker().unregister_request(trade.order.orderId)
        fresh = OrderService(self.pm, logging.getLogger("test_order_service"))
        fresh.attach(self.ib)
        self.assertEqual(
            get_request_tracker().get_bot_for_request(trade.order.orderId), "strategy_restarted")


class TestSchemaV4(unittest.TestCase):
    def test_fresh_schema_v4(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "v4.db")
            pm = PositionManager(path, logging.getLogger("test_order_service"))
            conn = sqlite3.connect(path)
            try:
                tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                user_ver = conn.execute("PRAGMA user_version").fetchone()[0]
                self.assertEqual(user_ver, 4)
                expected_tables = {
                    "bots", "entry_latches", "virtual_positions", "position_legs",
                    "orders", "fills", "order_events"
                }
                self.assertTrue(expected_tables <= tables)
            finally:
                conn.close()

    def test_older_schema_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "old.db")
            conn = sqlite3.connect(path)
            conn.execute("CREATE TABLE virtual_positions (position_id TEXT PRIMARY KEY)")
            conn.execute("PRAGMA user_version = 3")
            conn.commit()
            conn.close()

            with self.assertRaises(sqlite3.DatabaseError) as ctx:
                PositionManager(path, logging.getLogger("test_order_service"))
            self.assertIn("incompatible schema version", str(ctx.exception))

    def test_entry_latch_consumption(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "latch.db")
            pm = PositionManager(path, logging.getLogger("test_order_service"))
            self.assertFalse(pm.is_entry_latched("bot1", "2026-11-03"))
            self.assertTrue(pm.consume_entry_latch("bot1", "2026-11-03", "test trigger"))
            self.assertTrue(pm.is_entry_latched("bot1", "2026-11-03"))
            # Second consumption fails
            self.assertFalse(pm.consume_entry_latch("bot1", "2026-11-03", "second trigger"))
            # Different date succeeds
            self.assertTrue(pm.consume_entry_latch("bot1", "2026-11-04"))
            # Different bot succeeds
            self.assertTrue(pm.consume_entry_latch("bot2", "2026-11-03"))


class TestOrderServiceV4Features(OrderServiceTestCase):
    def test_cannot_modify_terminal_order(self):
        p = self.new_position()
        trade = self.place(p, LimitOrder("BUY", 1, 1.0), ROLE_OPEN)
        self.set_status(trade, "Filled", filled=1, avg=1.0)
        with self.assertRaises(ValueError) as ctx:
            self.service.modify_order(self.ib, trade, limit_price=1.1)
        self.assertIn("Cannot modify terminal order", str(ctx.exception))

    def test_racing_second_fill_moves_position_to_needs_reconciliation(self):
        p = self.new_position()
        trade_open = self.place(p, LimitOrder("BUY", 1, 1.0), ROLE_OPEN)
        self.set_status(trade_open, "Filled", filled=1, avg=1.0)
        self.assertEqual(self.pm.get_position(p.position_id).status, "OPEN")

        tp = self.place(p, LimitOrder("SELL", 1, 1.5), ROLE_TAKE_PROFIT)
        sl = self.place(p, StopOrder("SELL", 1, 0.5), ROLE_STOP_LOSS)

        # TP fills -> position CLOSED
        self.set_status(tp, "Filled", filled=1, avg=1.5)
        self.assertEqual(self.pm.get_position(p.position_id).status, "CLOSED")

        # Racing SL fill on closed position -> moves to NEEDS_RECONCILIATION
        self.set_status(sl, "Filled", filled=1, avg=0.5)
        self.assertEqual(self.pm.get_position(p.position_id).status, "NEEDS_RECONCILIATION")



if __name__ == "__main__":
    unittest.main()
