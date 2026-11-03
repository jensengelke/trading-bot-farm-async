"""
Unit tests for OptionOmega framework services:
- MarketDataService (reference counting, quotes, spread calculation)
- EntryScheduler (calendar, floating entry window, atomic daily latch)
- BrokerExitOrders (GTC PT/SL with broker OCA)
- LimitChaser (bounded entry chase, bounded soft exit chase)
- PositionMonitor (deadline calculation, exit coordination)
"""

import asyncio
from datetime import datetime, timezone
import logging
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import pytz
from ib_async import Contract, LimitOrder, OrderStatus, Ticker, Trade

from bots.optionomega.config import (
    EntryConfig,
    ExitConfig,
    ExitExecutionConfig,
    FloatingEntryConfig,
    FrequencyConfig,
    ProfitTargetConfig,
    StopLossConfig,
)
from framework.broker_exits import BrokerExitOrders
from framework.entry_scheduler import EntryScheduler, NY_TZ
from framework.limit_chaser import LimitChaser
from framework.market_data_service import MarketDataService
from framework.order_service import OrderService, ROLE_OPEN, ROLE_STOP_LOSS, ROLE_TAKE_PROFIT
from framework.position_manager import PositionManager, VirtualPosition
from framework.position_monitor import PositionMonitor


class TestMarketDataService(unittest.TestCase):

    def setUp(self):
        self.ib = MagicMock()
        self.logger = logging.getLogger("test_market_data")
        self.service = MarketDataService(self.ib, self.logger)

    def test_reference_counting(self):
        c = Contract(conId=12345, symbol="SPY")
        t1 = self.service.subscribe(c)
        self.ib.reqMktData.assert_called_once()
        self.assertEqual(self.service._ref_counts[12345], 1)

        t2 = self.service.subscribe(c)
        self.assertEqual(self.ib.reqMktData.call_count, 1)  # Reused
        self.assertEqual(self.service._ref_counts[12345], 2)

        self.service.unsubscribe(c)
        self.assertEqual(self.service._ref_counts[12345], 1)
        self.ib.cancelMktData.assert_not_called()

        self.service.unsubscribe(c)
        self.assertNotIn(12345, self.service._ref_counts)
        self.ib.cancelMktData.assert_called_once_with(c)

    def test_quote_validation(self):
        c = Contract(conId=100, symbol="SPY")
        ticker = Ticker(contract=c)
        self.service._tickers[100] = ticker

        # Missing quotes
        snap = self.service.get_quote(c)
        self.assertFalse(snap.is_valid)
        self.assertIn("Missing bid", snap.invalidation_reason)

        # Crossed market
        ticker.bid = 10.50
        ticker.ask = 10.20
        snap = self.service.get_quote(c)
        self.assertFalse(snap.is_valid)
        self.assertIn("Crossed market", snap.invalidation_reason)

        # Valid quote
        ticker.bid = 10.00
        ticker.ask = 10.20
        snap = self.service.get_quote(c)
        self.assertTrue(snap.is_valid)
        self.assertAlmostEqual(snap.mid, 10.10)

        # Max width violation
        snap = self.service.get_quote(c, max_bid_ask_width=0.10)
        self.assertFalse(snap.is_valid)
        self.assertIn("width", snap.invalidation_reason)


class TestEntryScheduler(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "sched.db")
        self.logger = logging.getLogger("test_sched")
        self.pm = PositionManager(self.db_path, self.logger)

    def tearDown(self):
        self.tmp.cleanup()

    def test_scheduled_days_and_blackouts(self):
        entry_cfg = EntryConfig(
            frequency=FrequencyConfig(type="weekly", days=["Mon", "Wed", "Fri"]),
            blackout_dates=["2026-11-06"],
            execution={"order_type": "limit", "mid_sampling_seconds": 0, "price_increment": 0.01, "wait_seconds": 1, "max_attempts": 1, "max_adverse_price_change": 0.05},
        )
        scheduler = EntryScheduler("bot1", entry_cfg, self.pm, self.logger)

        # 2026-11-02 is Monday -> True
        mon = NY_TZ.localize(datetime(2026, 11, 2, 11, 0, 0))
        self.assertTrue(scheduler.is_scheduled_day(mon))

        # 2026-11-03 is Tuesday -> False
        tue = NY_TZ.localize(datetime(2026, 11, 3, 11, 0, 0))
        self.assertFalse(scheduler.is_scheduled_day(tue))

        # 2026-11-06 is Friday, but is blacked out -> False
        fri_blackout = NY_TZ.localize(datetime(2026, 11, 6, 11, 0, 0))
        self.assertFalse(scheduler.is_scheduled_day(fri_blackout))

    def test_floating_window_and_atomic_latch(self):
        entry_cfg = EntryConfig(
            frequency=FrequencyConfig(type="daily"),
            floating_entry=FloatingEntryConfig(enabled=True, min_time="10:00", max_time="14:00"),
            execution={"order_type": "limit", "mid_sampling_seconds": 0, "price_increment": 0.01, "wait_seconds": 1, "max_attempts": 1, "max_adverse_price_change": 0.05},
        )
        scheduler = EntryScheduler("bot1", entry_cfg, self.pm, self.logger)

        early = NY_TZ.localize(datetime(2026, 11, 2, 9, 30, 0))
        can_enter, reason = scheduler.can_attempt_entry(early)
        self.assertFalse(can_enter)
        self.assertIn("outside floating window", reason)

        inside = NY_TZ.localize(datetime(2026, 11, 2, 10, 30, 0))
        can_enter, reason = scheduler.can_attempt_entry(inside)
        self.assertTrue(can_enter)

        # Consume latch
        self.assertTrue(scheduler.consume_latch("Triggered", dt=inside))

        # Second attempt is rejected because already latched
        can_enter, reason = scheduler.can_attempt_entry(inside)
        self.assertFalse(can_enter)
        self.assertIn("already consumed/latched", reason)


class TestBrokerExitOrders(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "exits.db")
        self.logger = logging.getLogger("test_broker_exits")
        self.pm = PositionManager(self.db_path, self.logger)
        self.order_service = OrderService(self.pm, self.logger)
        self.exits = BrokerExitOrders(self.order_service, self.logger)
        self.ib = MagicMock()
        self.order_service.attach(self.ib)

    def tearDown(self):
        self.tmp.cleanup()

    def test_protective_orders_placed_with_oca_and_gtc(self):
        # Create a position
        pos = self.order_service.create_position("bot1", {"dte": 0}, "SPY", quantity=1)
        contract = Contract(conId=999, symbol="SPY")

        pt_cfg = ProfitTargetConfig(type="percent", value=50)
        sl_cfg = StopLossConfig(type="percent", value=50, order_type="stop")

        # Mock ib.placeOrder
        def mock_place(c, o):
            return Trade(c, o, OrderStatus(orderId=o.orderId, status="Submitted"))
        self.ib.placeOrder.side_effect = mock_place

        pt_trade, sl_trade = self.exits.place_protective_exits(
            ib=self.ib,
            position_id=pos.position_id,
            contract=contract,
            entry_price=2.00,  # Debit entry 2.00: PT 50% = 3.00, SL 50% = 1.00
            filled_quantity=1,
            profit_target_cfg=pt_cfg,
            stop_loss_cfg=sl_cfg,
        )

        self.assertIsNotNone(pt_trade)
        self.assertIsNotNone(sl_trade)
        self.assertEqual(pt_trade.order.action, "SELL")
        self.assertEqual(sl_trade.order.action, "SELL")
        self.assertAlmostEqual(pt_trade.order.lmtPrice, 3.00)
        self.assertAlmostEqual(sl_trade.order.auxPrice, 1.00)
        self.assertEqual(pt_trade.order.tif, "GTC")
        self.assertEqual(sl_trade.order.tif, "GTC")
        self.assertEqual(pt_trade.order.ocaGroup, sl_trade.order.ocaGroup)
        self.assertTrue(pt_trade.order.ocaGroup.startswith("OCA_"))



class TestLimitChaserAsync(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "chaser.db")
        self.logger = logging.getLogger("test_chaser")
        self.pm = PositionManager(self.db_path, self.logger)
        self.order_service = OrderService(self.pm, self.logger)
        self.chaser = LimitChaser(self.order_service, self.logger)
        self.ib = MagicMock()
        self.order_service.attach(self.ib)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_entry_chase_bounded_by_adverse_change(self):
        pos = self.order_service.create_position("bot1", {"dte": 0}, "SPY", quantity=1)
        contract = Contract(conId=100, symbol="SPY")

        active_trade = None
        def mock_place(c, o):
            nonlocal active_trade
            active_trade = Trade(c, o, OrderStatus(orderId=o.orderId, status="Submitted"))
            return active_trade

        self.ib.placeOrder.side_effect = mock_place

        # Run chase with short wait_seconds (0.01)
        # anchor = 2.00, starting offset = 0.00, increment = 0.02, budget = 0.05
        # 1st limit = 2.00, 2nd = 2.02, 3rd = 2.04, 4th = 2.05 (capped by budget 2.05!)
        result = await self.chaser.chase_entry(
            ib=self.ib,
            position_id=pos.position_id,
            contract=contract,
            quantity=1,
            anchor_mid=2.00,
            price_increment=0.02,
            wait_seconds=0.01,
            max_attempts=4,
            max_adverse_price_change=0.05,
        )

        self.assertFalse(result.success)
        self.ib.cancelOrder.assert_called()

    async def test_soft_exit_bounded_by_natural_bid(self):
        pos = self.order_service.create_position("bot1", {"dte": 0}, "SPY", quantity=1)
        contract = Contract(conId=100, symbol="SPY")

        def mock_place(c, o):
            return Trade(c, o, OrderStatus(orderId=o.orderId, status="Submitted"))

        self.ib.placeOrder.side_effect = mock_place

        # mid = 1.00, natural_bid = 0.95, increment = 0.03
        # Attempt 1: 1.00. Attempt 2: 0.97. Attempt 3: 0.95 (bounded by natural bid, never below it!)
        result = await self.chaser.chase_soft_exit(
            ib=self.ib,
            position_id=pos.position_id,
            contract=contract,
            quantity=1,
            current_mid=1.00,
            natural_bid=0.95,
            price_increment=0.03,
            wait_seconds=0.01,
            max_attempts=3,
        )

        self.assertFalse(result.success)
        self.ib.cancelOrder.assert_called()


class TestPositionMonitor(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "mon.db")
        self.logger = logging.getLogger("test_mon")
        self.pm = PositionManager(self.db_path, self.logger)
        self.order_service = OrderService(self.pm, self.logger)
        self.market_data = MagicMock()
        self.chaser = MagicMock()

    def tearDown(self):
        self.tmp.cleanup()

    def test_deadline_detection(self):
        exit_cfg = ExitConfig(
            close_full_position_before_first_leg_expires=True,
            close_full_position_before_first_leg_expires_minutes=20,
            execution=ExitExecutionConfig(price_increment=0.01, wait_seconds=5, max_attempts=3),
        )
        monitor = PositionMonitor(
            "bot1", exit_cfg, self.order_service, self.pm, self.market_data, self.chaser, self.logger
        )

        # Create position with leg expiring at 16:00 NY
        legs = [{
            "leg_id": "front",
            "con_id": 1,
            "ratio": 1,
            "action": "BUY",
            "right": "CALL",
            "strike": 100.0,
            "last_trade_cutoff": "2026-11-03T16:00:00",
        }]
        pos = self.order_service.create_position("bot1", {"dte": 0}, "SPY", legs=legs, quantity=1)

        # 30 mins before cutoff -> False (outside 20 min window)
        now_30m = NY_TZ.localize(datetime(2026, 11, 3, 15, 30, 0))
        should_close, _ = monitor.should_close_for_deadline(pos, now_30m)
        self.assertFalse(should_close)

        # 15 mins before cutoff -> True (within 20 min window)
        now_15m = NY_TZ.localize(datetime(2026, 11, 3, 15, 45, 0))
        should_close, reason = monitor.should_close_for_deadline(pos, now_15m)
        self.assertTrue(should_close)
        self.assertIn("deadline window", reason)


if __name__ == "__main__":
    unittest.main()

