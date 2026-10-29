"""
Unit tests for Telegram Bot and Framework Integration.
"""

import unittest
from unittest.mock import MagicMock, patch, AsyncMock
from ib_async import Trade, Order, OrderStatus, Contract, Execution, Fill, CommissionReport

from framework.telegram_client import TelegramClient, escape_html
from framework.config.system_config import SystemConfig, TelegramSystemConfig, ConfigModel
from bots.telegram.config import TelegramBotConfig
from bots.telegram.formatters import (
    format_contract_summary,
    format_order_placement,
    format_order_fill,
    format_order_cancelled,
    format_order_status_change,
    format_startup_message,
    format_connected_readiness_message
)
from bots.telegram.bot import Bot


class TestTelegramClient(unittest.TestCase):
    def test_escape_html(self):
        self.assertEqual(escape_html("AAPL <test> & 'more'"), "AAPL &lt;test&gt; &amp; 'more'")
        self.assertEqual(escape_html(None), "")
        self.assertEqual(escape_html(123), "123")

    def test_chunk_message_short(self):
        msg = "Short message"
        chunks = TelegramClient._chunk_message(msg, 100)
        self.assertEqual(chunks, ["Short message"])

    def test_chunk_message_long(self):
        lines = [f"Line {i}" for i in range(200)]
        long_msg = "\n".join(lines)
        chunks = TelegramClient._chunk_message(long_msg, 500)
        self.assertTrue(len(chunks) > 1)
        for c in chunks:
            self.assertTrue(len(c) <= 500)

    @patch("framework.telegram_client.urllib.request.urlopen")
    def test_send_message_sync(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"ok": true, "result": {"message_id": 1}}'
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        client = TelegramClient("dummy_token", "12345")
        res = client._post_sync("sendMessage", {"chat_id": "12345", "text": "hello"})
        self.assertTrue(res.get("ok"))


class TestTelegramConfig(unittest.TestCase):
    def test_bot_config_valid(self):
        data = {
            "type": "telegram",
            "connection": "default",
            "notifications": {
                "enabled": True,
                "notify_on_placement": True,
                "notify_on_fill": True
            }
        }
        cfg = TelegramBotConfig(**data)
        self.assertEqual(cfg.type, "telegram")
        self.assertEqual(cfg.connection, "default")
        self.assertTrue(cfg.notifications.notify_on_fill)

    def test_system_config_telegram_normalization(self):
        data = {
            "token": "tok123",
            "chat_id": 99999
        }
        sys_cfg = TelegramSystemConfig(**data)
        self.assertIn("default", sys_cfg.connections)
        self.assertEqual(sys_cfg.connections["default"].token, "tok123")
        self.assertEqual(sys_cfg.connections["default"].chat_id, "99999")


class TestTelegramFormatters(unittest.TestCase):
    def test_format_contract_summary(self):
        c_stk = Contract(symbol="AAPL", secType="STK")
        self.assertEqual(format_contract_summary(c_stk), "AAPL (Stock)")

        c_opt = Contract(symbol="SPX", secType="OPT", lastTradeDateOrContractMonth="20261120", strike=5900, right="C")
        self.assertEqual(format_contract_summary(c_opt), "SPX 20261120 5900C")

    def test_format_order_placement(self):
        order = Order(orderId=101, action="BUY", totalQuantity=2, orderType="LMT", lmtPrice=2.50, orderRef="test_ref")
        contract = Contract(symbol="SPX", secType="BAG")
        status = OrderStatus(status="Submitted", filled=0, remaining=2)
        trade = Trade(contract=contract, order=order, orderStatus=status)

        text = format_order_placement(trade, account="DU12345")
        self.assertIn("🟢 <b>Order Submitted</b>", text)
        self.assertIn("BUY 2 LMT @ 2.50", text)
        self.assertIn("test_ref", text)
        self.assertIn("DU12345", text)

    def test_format_order_fill(self):
        order = Order(orderId=102, action="SELL", totalQuantity=1, orderRef="test_fill")
    @patch("framework.bot_base.get_bot_logger")
    def test_bot_initialization_and_filtering(self, mock_logger):
        mock_sys_config = MagicMock()
        mock_sys_config.get.side_effect = lambda k, d=None: {
            "telegram.connections.default": {"token": "123:ABC", "chat_id": "456"},
            "connection.selected_account": "DU123"
        }.get(k, d)

        bot_cfg = {
            "type": "telegram",
            "connection": "default",
            "notifications": {
                "enabled": True,
                "notify_on_placement": True,
                "exclude_order_refs": ["ignore_me"]
            }
        }
        mock_mgr = MagicMock()
        bot = Bot("telegram_test", bot_cfg, mock_sys_config, mock_mgr)
        self.assertEqual(bot.validated_config.connection, "default")

        # Test filtering
        order_ignored = Order(orderId=1, orderRef="ignore_me_now")
        trade_ignored = Trade(order=order_ignored, contract=Contract())
        self.assertTrue(bot._should_filter_trade(trade_ignored))

        order_allowed = Order(orderId=2, orderRef="trade_regular")
        trade_allowed = Trade(order=order_allowed, contract=Contract())
        self.assertFalse(bot._should_filter_trade(trade_allowed))

        # Test open order callback dispatches task
        with patch.object(bot, "_dispatch_message") as mock_dispatch:
            bot._running = True
            bot._on_open_order(trade_allowed)
            self.assertIn(2, bot._notified_placements)
            mock_dispatch.assert_called_once()

        # Test order status transition
        with patch.object(bot, "_dispatch_message") as mock_dispatch:
            status_presubmit = OrderStatus(status="PreSubmitted", filled=0, remaining=2)
            trade_allowed.orderStatus = status_presubmit
            bot._on_order_status(trade_allowed)
            self.assertEqual(bot._known_order_statuses[2], "PreSubmitted")
            mock_dispatch.assert_not_called()  # First transition recorded without prior status

            # Status changes to Submitted
            status_submit = OrderStatus(status="Submitted", filled=0, remaining=2)
            trade_allowed.orderStatus = status_submit
            bot._on_order_status(trade_allowed)
            self.assertEqual(bot._known_order_statuses[2], "Submitted")
            mock_dispatch.assert_called_once()

        # Test order cancellation
        with patch.object(bot, "_dispatch_message") as mock_dispatch:
            status_cancel = OrderStatus(status="Cancelled", filled=0, remaining=2)
            trade_allowed.orderStatus = status_cancel
            bot._on_order_status(trade_allowed)
            self.assertEqual(bot._known_order_statuses[2], "Cancelled")
            mock_dispatch.assert_called_once()

        # Test order execution fill
        with patch.object(bot, "_dispatch_message") as mock_dispatch:
            exec_obj = Execution(execId="EXEC_100", side="BOT", shares=2, price=2.50)
            fill = Fill(contract=Contract(), execution=exec_obj, commissionReport=None, time=None)
            bot._on_exec_details(trade_allowed, fill)
            self.assertIn("EXEC_100", bot._processed_executions)
            mock_dispatch.assert_called_once()

            # Duplicate execution should be ignored
            mock_dispatch.reset_mock()
            bot._on_exec_details(trade_allowed, fill)
            mock_dispatch.assert_not_called()

    def test_format_connected_readiness_message(self):
        account_summary = {
            "NetLiquidation": {"value": "105432.50", "currency": "USD"},
            "TotalCashValue": {"value": "54321.00", "currency": "USD"},
            "AvailableFunds": {"value": "80000.00", "currency": "USD"},
            "BuyingPower": {"value": "160000.00", "currency": "USD"},
        }
        from ib_async import Position
        pos = Position(
            account="U22220131",
            contract=Contract(symbol="AAPL", secType="STK"),
            position=100.0,
            avgCost=150.25
        )
        order = Order(orderId=10, action="BUY", totalQuantity=5, orderType="LMT", lmtPrice=155.0, orderRef="strat_1")
        trade = Trade(
            contract=Contract(symbol="AAPL", secType="STK"),
            order=order,
            orderStatus=OrderStatus(status="Submitted")
        )

        msg = format_connected_readiness_message(
            bot_id="telegram_notifier",
            account="U22220131",
            connection_name="default",
            account_summary=account_summary,
            positions=[pos],
            open_trades=[trade]
        )

        self.assertIn("🟢 <b>Telegram Notifier Connected & Ready</b>", msg)
        self.assertIn("U22220131", msg)
        self.assertIn("105,432.50 USD", msg)
        self.assertIn("AAPL (Stock)", msg)
        self.assertIn("+100 @ 150.25", msg)
        self.assertIn("BUY 5 AAPL (Stock)", msg)
        self.assertIn("LMT @ 155.00", msg)

    def test_format_connected_readiness_empty(self):
        msg = format_connected_readiness_message(
            bot_id="telegram_notifier",
            account="U22220131",
            connection_name="default",
            account_summary={},
            positions=[],
            open_trades=[]
        )
        self.assertIn("Balance details not available", msg)
        self.assertIn("No open positions", msg)
        self.assertIn("No open orders", msg)

    @patch("framework.bot_base.get_bot_logger")
    def test_send_connected_readiness_message(self, mock_logger):
        import asyncio
        mock_sys_config = MagicMock()
        mock_sys_config.get.side_effect = lambda k, d=None: {
            "telegram.connections.default": {"token": "123:ABC", "chat_id": "456"},
            "connection.selected_account": "U22220131"
        }.get(k, d)

        bot_cfg = {
            "type": "telegram",
            "connection": "default",
            "notifications": {
                "enabled": True
            }
        }
        mock_mgr = MagicMock()
        bot = Bot("telegram_test", bot_cfg, mock_sys_config, mock_mgr)

        # Mock IB connection methods
        mock_ib = MagicMock()
        mock_ib.isConnected.return_value = True

        async def mock_summary(acct):
            from ib_async import AccountValue
            return [
                AccountValue(account="U22220131", tag="NetLiquidation", value="50000", currency="USD", modelCode=""),
                AccountValue(account="U22220131", tag="TotalCashValue", value="20000", currency="USD", modelCode="")
            ]
        mock_ib.accountSummaryAsync = mock_summary
        mock_ib.portfolio.return_value = []

        async def mock_positions():
            return []
        mock_ib.reqPositionsAsync = mock_positions

        async def mock_open_orders():
            return []
        mock_ib.reqAllOpenOrdersAsync = mock_open_orders

        bot.ib = mock_ib
        bot.telegram_client.send_message = AsyncMock()

        asyncio.run(bot._send_connected_readiness_message())
        bot.telegram_client.send_message.assert_called_once()
        sent_text = bot.telegram_client.send_message.call_args[0][0]
        self.assertIn("🟢 <b>Telegram Notifier Connected & Ready</b>", sent_text)
        self.assertIn("50,000.00 USD", sent_text)
        self.assertIn("No open positions", sent_text)
        self.assertIn("No open orders", sent_text)



if __name__ == "__main__":
    unittest.main()
