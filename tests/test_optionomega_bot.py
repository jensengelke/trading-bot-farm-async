"""
Unit tests for OptionOmega Bot instantiation and lifecycle.
"""

import logging
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import yaml

from bots.optionomega.bot import Bot
from framework.config.system_config import SystemConfig


class TestOptionOmegaBotLifecycle(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        fixture_path = Path("docs/fixtures/optionomega_calendar_example.yaml")
        with open(fixture_path, "r", encoding="utf-8") as f:
            self.bot_config = yaml.safe_load(f)

        self.system_config = MagicMock()
        self.system_config.get.side_effect = lambda k, default=None: {
            "connection.host": "127.0.0.1",
            "connection.port": 7497,
            "connection.client_id": 1,
            "ibc": None,
            "database.path": ":memory:",
        }.get(k, default)

        self.ib_conn_mgr = MagicMock()
        self.mock_ib = MagicMock()
        self.mock_ib.trades.return_value = []
        self.ib_conn_mgr.connect = AsyncMock(return_value=self.mock_ib)

    @patch("framework.decorators.get_bot_logger")
    @patch("framework.bot_base.get_bot_logger")
    async def test_bot_instantiation_and_lifecycle(self, mock_base_logger, mock_dec_logger):
        test_logger = logging.getLogger("test_oo_bot")
        mock_base_logger.return_value = test_logger
        mock_dec_logger.return_value = test_logger
        bot = Bot(
            bot_id="oo_calendar_test",
            config=self.bot_config,
            system_config=self.system_config,
            ib_connection_manager=self.ib_conn_mgr,
        )
        self.assertEqual(bot.bot_id, "oo_calendar_test")
        self.assertEqual(bot.validated_config.name, "SPY illustrative calendar")

        # Stop immediately so start doesn't loop forever
        bot._stop_requested = True
        await bot.start()

        self.ib_conn_mgr.connect.assert_called_once()
        self.assertIsNotNone(bot.scheduler)
        self.assertIsNotNone(bot.position_monitor)
        self.assertIsNotNone(bot.broker_exits)
        self.assertIsNotNone(bot.limit_chaser)

        await bot.stop()
        self.assertIsNone(bot.ib)





if __name__ == "__main__":
    unittest.main()
