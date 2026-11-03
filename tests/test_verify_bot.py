"""
Unit tests for Verify Bot (bots/verify/bot.py and bots/verify/config.py).
"""

import asyncio
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from ib_async import Contract
from ib_async.contract import Index, Option, Stock

from bots.verify.bot import Bot
from bots.verify.config import SymbolConfig, VerifyBotConfig


class TestVerifyBotConfig(unittest.TestCase):
    def test_symbol_config_defaults(self):
        cfg = SymbolConfig(ticker="TSLA")
        self.assertEqual(cfg.ticker, "TSLA")
        self.assertEqual(cfg.exchange, "SMART")
        self.assertEqual(cfg.currency, "USD")
        self.assertEqual(cfg.right, "C")
        self.assertIsNone(cfg.security_type)

    def test_verify_bot_config_options_without_explicit_right(self):
        data = {
            "type": "verify",
            "security_type": "option",
            "symbols": [
                {"ticker": "TSLA", "exchange": "SMART", "currency": "USD"},
                {"ticker": "DAX", "exchange": "EUREX", "currency": "EUR"},
            ],
        }
        cfg = VerifyBotConfig(**data)
        self.assertEqual(cfg.security_type, "option")
        self.assertEqual(len(cfg.symbols), 2)
        self.assertEqual(cfg.symbols[0].right, "C")
        self.assertEqual(cfg.symbols[1].right, "C")

    def test_symbol_config_right_validation(self):
        with self.assertRaises(ValueError):
            SymbolConfig(ticker="TSLA", right="INVALID")


class TestVerifyBotLogic(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.logger_patch1 = patch("framework.bot_base.get_bot_logger")
        self.logger_patch2 = patch("framework.decorators.get_bot_logger")
        self.mock_logger = MagicMock()
        self.logger_patch1.start().return_value = self.mock_logger
        self.logger_patch2.start().return_value = self.mock_logger
        self.addCleanup(self.logger_patch1.stop)
        self.addCleanup(self.logger_patch2.stop)

        self.bot_id = "test_verify"
        self.raw_config = {
            "type": "verify",
            "security_type": "option",
            "symbols": [
                {"ticker": "TSLA", "exchange": "SMART", "currency": "USD"},
                {"ticker": "DAX", "exchange": "EUREX", "currency": "EUR"},
            ],
        }
        self.mock_sys_config = MagicMock()
        self.mock_sys_config.get.side_effect = lambda k: {
            "connection.host": "127.0.0.1",
            "connection.port": 7496,
            "connection.client_id": 1,
            "ibc": None,
        }.get(k)
        self.mock_conn_mgr = MagicMock()
        self.mock_ib = MagicMock()
        self.mock_conn_mgr.connect = AsyncMock(return_value=self.mock_ib)

        self.bot = Bot(
            self.bot_id,
            self.raw_config,
            self.mock_sys_config,
            self.mock_conn_mgr,
        )
        self.bot.ib = self.mock_ib

    def test_find_target_friday_expiration(self):
        today = datetime.now().date()
        if today.weekday() <= 4:
            days = 4 - today.weekday()
        else:
            days = 4 - today.weekday() + 7
        target_friday = today + timedelta(days=days)
        target_friday_str = target_friday.strftime("%Y%m%d")

        expirations = [
            (today - timedelta(days=10)).strftime("%Y%m%d"),
            target_friday_str,
            (target_friday + timedelta(days=7)).strftime("%Y%m%d"),
        ]

        found = self.bot._find_target_friday_expiration(expirations)
        self.assertEqual(found, target_friday_str)

    def test_find_target_friday_expiration_holiday_fallback(self):
        today = datetime.now().date()
        if today.weekday() <= 4:
            days = 4 - today.weekday()
        else:
            days = 4 - today.weekday() + 7
        target_friday = today + timedelta(days=days)

        next_friday_str = (target_friday + timedelta(days=7)).strftime("%Y%m%d")
        expirations = [
            next_friday_str,
            (target_friday + timedelta(days=14)).strftime("%Y%m%d"),
        ]

        found = self.bot._find_target_friday_expiration(expirations)
        self.assertEqual(found, next_friday_str)

    async def test_resolve_underlying_stock(self):
        qualified_stock = Stock("TSLA", "SMART", "USD")
        qualified_stock.conId = 76792991
        qualified_stock.secType = "STK"

        self.mock_ib.qualifyContractsAsync = AsyncMock(return_value=[qualified_stock])

        contract = await self.bot._resolve_underlying("TSLA", "SMART", "USD", None)
        self.assertEqual(contract.secType, "STK")
        self.assertEqual(contract.conId, 76792991)

    async def test_resolve_underlying_index(self):
        qualified_index = Index("DAX", "DTB", "EUR")
        qualified_index.conId = 1001
        qualified_index.secType = "IND"

        async def mock_qualify(c):
            if isinstance(c, Index):
                return [qualified_index]
            return [None]

        self.mock_ib.qualifyContractsAsync = AsyncMock(side_effect=mock_qualify)

        contract = await self.bot._resolve_underlying("DAX", "EUREX", "EUR", None)
        self.assertEqual(contract.secType, "IND")
        self.assertEqual(contract.conId, 1001)

    async def test_create_option_contract_and_market_data(self):
        qualified_index = Index("DAX", "DTB", "EUR")
        qualified_index.conId = 1001
        qualified_index.secType = "IND"

        async def mock_qualify(c):
            if isinstance(c, Index):
                return [qualified_index]
            if isinstance(c, Option):
                c.conId = 5555
                return [c]
            return [None]

        self.mock_ib.qualifyContractsAsync = AsyncMock(side_effect=mock_qualify)

        today = datetime.now().date()
        if today.weekday() <= 4:
            days = 4 - today.weekday()
        else:
            days = 4 - today.weekday() + 7
        target_friday_str = (today + timedelta(days=days)).strftime("%Y%m%d")

        mock_chain = MagicMock()
        mock_chain.exchange = "EUREX"
        mock_chain.tradingClass = "ODAX"
        mock_chain.expirations = [target_friday_str]
        mock_chain.strikes = [19000.0, 19500.0, 20000.0]

        self.mock_ib.reqSecDefOptParamsAsync = AsyncMock(return_value=[mock_chain])

        mock_underlying_ticker = MagicMock()
        mock_underlying_ticker.last = 19510.0
        mock_underlying_ticker.midpoint.return_value = 19510.0
        mock_underlying_ticker.close = 19500.0
        mock_underlying_ticker.bid = 19505.0
        mock_underlying_ticker.ask = 19515.0

        self.mock_ib.reqMktData.return_value = mock_underlying_ticker

        opt = await self.bot._create_option_contract({
            "ticker": "DAX",
            "exchange": "EUREX",
            "currency": "EUR",
            "right": "C",
        })

        self.assertEqual(opt.secType, "OPT")
        self.assertEqual(opt.symbol, "DAX")
        self.assertEqual(opt.strike, 19500.0)
        self.assertEqual(opt.right, "C")
        self.assertEqual(opt.lastTradeDateOrContractMonth, target_friday_str)
        self.mock_ib.reqSecDefOptParamsAsync.assert_awaited_once_with(
            underlyingSymbol="DAX",
            futFopExchange="",
            underlyingSecType="IND",
            underlyingConId=1001,
        )

    async def test_start_logs_option_bid_ask(self):
        qualified_stock = Stock("TSLA", "SMART", "USD")
        qualified_stock.conId = 76792991
        qualified_stock.secType = "STK"

        today = datetime.now().date()
        if today.weekday() <= 4:
            days = 4 - today.weekday()
        else:
            days = 4 - today.weekday() + 7
        target_friday_str = (today + timedelta(days=days)).strftime("%Y%m%d")

        mock_chain = MagicMock()
        mock_chain.exchange = "SMART"
        mock_chain.tradingClass = "TSLA"
        mock_chain.expirations = [target_friday_str]
        mock_chain.strikes = [200.0, 210.0, 220.0]

        async def mock_qualify(c):
            if isinstance(c, Stock):
                return [qualified_stock]
            if isinstance(c, Option):
                c.conId = 8888
                c.localSymbol = "TSLA  261106C00210000"
                return [c]
            return [None]

        self.mock_ib.qualifyContractsAsync = AsyncMock(side_effect=mock_qualify)
        self.mock_ib.reqSecDefOptParamsAsync = AsyncMock(return_value=[mock_chain])

        mock_ticker = MagicMock()
        mock_ticker.last = 210.5
        mock_ticker.midpoint.return_value = 210.5
        mock_ticker.close = 210.0
        mock_ticker.bid = 5.20
        mock_ticker.ask = 5.30
        self.mock_ib.reqMktData.return_value = mock_ticker

        single_symbol_config = {
            "type": "verify",
            "security_type": "option",
            "symbols": [
                {"ticker": "TSLA", "exchange": "SMART", "currency": "USD"}
            ],
        }
        bot = Bot("test_verify", single_symbol_config, self.mock_sys_config, self.mock_conn_mgr)
        bot.ib = self.mock_ib

        with patch.object(bot.logger, "info") as mock_log:
            await bot.start()
            self.mock_ib.cancelMktData.assert_called()
            logged_messages = [call.args[0] for call in mock_log.call_args_list if call.args]
            self.assertTrue(any("Bid=5.2, Ask=5.3" in str(m) for m in logged_messages))


if __name__ == "__main__":
    unittest.main()
