"""
Unit tests for DAX Spread Observation Bot and Option Spreads Framework.
"""

import unittest
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime, timezone
from ib_async import Contract, Trade, Order, OrderStatus, ComboLeg

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
from bots.dax_spread_observation.bot import Bot


class MockPortfolioItem:
    def __init__(self, symbol, right, strike, expiry, position, avg_cost=100.0, unrealized_pnl=50.0, con_id=1000):
        self.contract = Contract(
            conId=con_id,
            symbol=symbol,
            secType="OPT",
            lastTradeDateOrContractMonth=expiry,
            strike=float(strike),
            right=right,
            multiplier="5"
        )
        self.position = float(position)
        self.avgCost = float(avg_cost)
        self.averageCost = float(avg_cost)
        self.unrealizedPNL = float(unrealized_pnl)


class TestOptionSpreadsFramework(unittest.TestCase):
    def test_spread_properties_call(self):
        short = MockPortfolioItem("DAX", "C", 26300, "20261106", -4.0, avg_cost=164.0, unrealized_pnl=-500.0)
        long = MockPortfolioItem("DAX", "C", 26550, "20261106", 4.0, avg_cost=76.0, unrealized_pnl=200.0)

        spread = OptionSpreadPosition(
            spread_type="BEAR_CALL_SPREAD",
            symbol="DAX",
            expiration="20261106",
            right="C",
            quantity=4,
            short_leg=short,
            long_leg=long,
            short_strike=26300.0,
            long_strike=26550.0,
            underlying_price=26150.0,
            unrealized_pnl=-300.0
        )

        self.assertEqual(spread.wing_width, 250.0)
        # For call, buffer = short_strike - underlying = 26300 - 26150 = 150 pts
        self.assertEqual(spread.short_strike_buffer_points, 150.0)
        self.assertAlmostEqual(spread.short_strike_buffer_percent, (150.0 / 26150.0) * 100.0, places=2)
        self.assertFalse(spread.is_short_strike_tested(buffer_points=50.0))
        self.assertTrue(spread.is_short_strike_tested(buffer_points=200.0))
        self.assertFalse(spread.is_long_strike_tested())

    def test_spread_properties_put(self):
        short = MockPortfolioItem("DAX", "P", 24150, "20261106", -5.0, avg_cost=290.0, unrealized_pnl=500.0)
        long = MockPortfolioItem("DAX", "P", 23900, "20261106", 5.0, avg_cost=230.0, unrealized_pnl=-400.0)

        spread = OptionSpreadPosition(
            spread_type="BULL_PUT_SPREAD",
            symbol="DAX",
            expiration="20261106",
            right="P",
            quantity=5,
            short_leg=short,
            long_leg=long,
            short_strike=24150.0,
            long_strike=23900.0,
            underlying_price=24160.0,
            unrealized_pnl=100.0
        )

        self.assertEqual(spread.wing_width, 250.0)
        # For put, buffer = underlying - short_strike = 24160 - 24150 = 10 pts
        self.assertEqual(spread.short_strike_buffer_points, 10.0)
        # Within 50 points, so tested!
        self.assertTrue(spread.is_short_strike_tested(buffer_points=50.0))
        self.assertFalse(spread.is_long_strike_tested())

        # If price drops to 23800, long strike is breached
        spread.underlying_price = 23800.0
        self.assertTrue(spread.is_long_strike_tested())

    def test_identify_spread_positions_standard(self):
        items = [
            MockPortfolioItem("DAX", "C", 26300, "20261106", -4.0, con_id=1),
            MockPortfolioItem("DAX", "C", 26550, "20261106", 4.0, con_id=2),
            MockPortfolioItem("DAX", "P", 24150, "20261106", -5.0, con_id=3),
            MockPortfolioItem("DAX", "P", 23900, "20261106", 5.0, con_id=4),
            # Non-matching symbol
            MockPortfolioItem("SPX", "C", 5800, "20261106", -1.0, con_id=5),
        ]

        spreads = identify_spread_positions(items, symbol="DAX")
        self.assertEqual(len(spreads), 2)

        call_spread = next(s for s in spreads if s.right == "C")
        self.assertEqual(call_spread.spread_type, "BEAR_CALL_SPREAD")
        self.assertEqual(call_spread.quantity, 4)
        self.assertEqual(call_spread.short_strike, 26300.0)
        self.assertEqual(call_spread.long_strike, 26550.0)

        put_spread = next(s for s in spreads if s.right == "P")
        self.assertEqual(put_spread.spread_type, "BULL_PUT_SPREAD")
        self.assertEqual(put_spread.quantity, 5)
        self.assertEqual(put_spread.short_strike, 24150.0)
        self.assertEqual(put_spread.long_strike, 23900.0)

    def test_identify_spread_positions_rolling_multiple(self):
        # Rolling scenario: two put spreads for the same expiry
        items = [
            MockPortfolioItem("DAX", "P", 23550, "20261113", -6.0, con_id=11),
            MockPortfolioItem("DAX", "P", 23300, "20261113", 6.0, con_id=12),
            MockPortfolioItem("DAX", "P", 23750, "20261113", -3.0, con_id=13),
            MockPortfolioItem("DAX", "P", 23500, "20261113", 3.0, con_id=14),
        ]

        spreads = identify_spread_positions(items, symbol="DAX")
        self.assertEqual(len(spreads), 2)
        strikes = [(s.short_strike, s.long_strike, s.quantity) for s in spreads]
        self.assertIn((23750.0, 23500.0, 3), strikes)
        self.assertIn((23550.0, 23300.0, 6), strikes)

    def test_audit_take_profit_orders_combo(self):
        short = MockPortfolioItem("DAX", "C", 26300, "20261106", -4.0, con_id=101)
        long = MockPortfolioItem("DAX", "C", 26550, "20261106", 4.0, con_id=102)
        spread = OptionSpreadPosition("BEAR_CALL_SPREAD", "DAX", "20261106", "C", 4, short, long, 26300, 26550)

        # Case A: TWS Close order (action=SELL, negative credit limit price -3.00)
        bag_contract = Contract(secType="BAG", symbol="DAX")
        bag_contract.comboLegs = [ComboLeg(conId=101, ratio=1, action="SELL"), ComboLeg(conId=102, ratio=1, action="BUY")]
        order = Order(orderId=501, action="SELL", totalQuantity=4, orderType="LMT", lmtPrice=-3.0, tif="GTC")
        trade = Trade(contract=bag_contract, order=order, orderStatus=OrderStatus(status="Submitted"))

        audit_take_profit_orders([spread], [trade])
        self.assertTrue(spread.has_take_profit)
        self.assertEqual(spread.take_profit_price, -3.0)
        self.assertEqual(spread.take_profit_action, "SELL")
        self.assertEqual(spread.take_profit_tif, "GTC")

    def test_audit_take_profit_orders_single_leg(self):
        short = MockPortfolioItem("DAX", "P", 24150, "20261106", -5.0, con_id=201)
        long = MockPortfolioItem("DAX", "P", 23900, "20261106", 5.0, con_id=202)
        spread = OptionSpreadPosition("BULL_PUT_SPREAD", "DAX", "20261106", "P", 5, short, long, 24150, 23900)

        opt_contract = Contract(conId=201, symbol="DAX", secType="OPT")
        order = Order(orderId=502, action="BUY", totalQuantity=5, orderType="LMT", lmtPrice=10.0)
        trade = Trade(contract=opt_contract, order=order, orderStatus=OrderStatus(status="PreSubmitted"))

        audit_take_profit_orders([spread], [trade])
        self.assertTrue(spread.has_take_profit)
        self.assertEqual(spread.take_profit_price, 10.0)


class TestDaxSpreadFormatters(unittest.TestCase):
    def setUp(self):
        short = MockPortfolioItem("DAX", "C", 26300, "20261106", -4.0, con_id=1)
        long = MockPortfolioItem("DAX", "C", 26550, "20261106", 4.0, con_id=2)
        self.spread = OptionSpreadPosition(
            "BEAR_CALL_SPREAD", "DAX", "20261106", "C", 4, short, long, 26300.0, 26550.0,
            underlying_price=26180.0, unrealized_pnl=-150.0, short_delta=-0.395,
            has_take_profit=True, take_profit_price=35.0, take_profit_action="SELL",
            take_profit_type="LMT", take_profit_qty=4, take_profit_tif="GTC"
        )

    def test_format_delta_breach(self):
        msg = format_delta_breach_alert(self.spread, short_delta=-0.395, threshold=0.38)
        self.assertIn("DAX Short Delta Alert", msg)
        self.assertIn("0.395", msg)
        self.assertIn("26300C", msg)
        self.assertIn("26,180.00", msg)

    def test_format_strike_tested(self):
        msg = format_strike_tested_alert(self.spread, is_long=False, strike=26300.0, underlying_price=26280.0, buffer_pts=20.0)
        self.assertIn("DAX Strike TESTED Alert", msg)
        self.assertIn("SHORT STRIKE", msg)
        self.assertIn("26,280.00", msg)
        self.assertIn("20.0 pts", msg)

    def test_format_daily_spread_summary(self):
        bag_contract = Contract(secType="BAG", symbol="DAX")
        bag_contract.comboLegs = [ComboLeg(conId=1, ratio=1, action="SELL"), ComboLeg(conId=2, ratio=1, action="BUY")]
        order = Order(orderId=501, action="SELL", totalQuantity=4, orderType="LMT", lmtPrice=-3.0, tif="GTC")
        trade = Trade(contract=bag_contract, order=order, orderStatus=OrderStatus(status="Submitted"))

        msg = format_daily_spread_summary(
            [self.spread],
            underlying_price=26180.0,
            account="U10702503",
            open_trades=[trade],
            contract_map={1: self.spread.short_leg.contract, 2: self.spread.long_leg.contract}
        )
        self.assertIn("DAX Option Spreads Daily Summary", msg)
        self.assertIn("U10702503", msg)
        self.assertIn("26,180.00", msg)
        self.assertIn('26300C', msg)
        self.assertIn('26550C', msg)
        self.assertIn('short leg Δ: <code>0.395</code>', msg)
        self.assertIn('TP: SELL 4x @ 35.00 (LMT GTC)', msg)
        self.assertIn("Open DAX Orders (1)", msg)
        self.assertIn("SELL 4x DAX Combo 26300C / 26550C", msg)
        self.assertIn("@ -3.00 (LMT GTC)", msg)


class TestDaxSpreadObservationBot(unittest.IsolatedAsyncioTestCase):
    @patch('framework.bot_base.get_bot_logger')
    async def test_bot_evaluation_cycle(self, mock_logger):
        mock_logger.return_value = MagicMock()
        mock_sys_config = MagicMock()
        mock_sys_config.get.side_effect = lambda k, d=None: {
            "telegram.connections.default": {"token": "123:ABC", "chat_id": "456"},
            "connection.selected_account": "U10702503"
        }.get(k, d)

        bot_cfg = {
            "type": "dax_spread_observation",
            "name": "Test DAX Observer",
            "connection": "default",
            "max_short_delta": 0.38,
            "strike_tested_buffer_points": 50.0,
            "monitoring_interval_seconds": 60.0
        }

        mock_mgr = MagicMock()
        bot = Bot("test_dax_observer", bot_cfg, mock_sys_config, mock_mgr)
        bot.telegram_client.send_message = AsyncMock()

        # Mock IB
        mock_ib = MagicMock()
        mock_ib.isConnected.return_value = True

        short = MockPortfolioItem("DAX", "C", 26300, "20261106", -4.0, con_id=101)
        long = MockPortfolioItem("DAX", "C", 26550, "20261106", 4.0, con_id=102)
        mock_ib.portfolio.return_value = [short, long]
        mock_ib.openTrades.return_value = []

        bot.ib = mock_ib
        bot.market_data = MagicMock()
        
        # Mock quote for short leg with high delta (0.41 > 0.38) and price close to strike
        from framework.market_data_service import QuoteSnapshot
        snap = QuoteSnapshot(
            contract=short.contract,
            delta=0.41,
            last=95.0,
            mid=95.0
        )
        bot.market_data.get_quote.return_value = snap
        bot._get_underlying_price = MagicMock(return_value=26270.0)  # 30 pts away, within 50 pt tested buffer

        # Run one monitoring cycle
        await bot._monitor_cycle()

        # Check that alerts were dispatched
        # Both delta breach and strike tested should fire
        self.assertEqual(len(bot._last_alert_times), 2)
        self.assertIn("delta_20261106_C_26300.0", bot._last_alert_times)
        self.assertIn("short_tested_20261106_C_26300.0", bot._last_alert_times)
        
        # Test cooldown: running cycle immediately again should NOT alert again
        bot.telegram_client.send_message.reset_mock()
        await bot._monitor_cycle()
        # Cooldown prevents repeated send
        bot.telegram_client.send_message.assert_not_called()


if __name__ == "__main__":
    unittest.main()




