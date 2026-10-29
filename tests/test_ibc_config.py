import unittest
from pathlib import Path
from framework.config.system_config import SystemConfig
from framework.bot_manager import BotManager
from framework.logging_config import initialize_logging, get_logging_config


class TestIbcConfig(unittest.TestCase):
    """Test IBC multi-instance configuration for live and live-read."""

    @classmethod
    def setUpClass(cls):
        initialize_logging("test_ibc")

    @classmethod
    def tearDownClass(cls):
        cfg = get_logging_config()
        if cfg:
            cfg.shutdown()

    def test_live_config(self):
        """Verify config/live has port 4001 and matching IBC ini with OverrideTwsApiPort=4001."""
        sys_config = SystemConfig("config/live")
        port = sys_config.get("connection.port")
        self.assertEqual(port, 4001)

        ibc_enabled = sys_config.get("ibc.enabled")
        self.assertTrue(ibc_enabled)

        ibc_ini = sys_config.get("ibc.ibcIni")
        self.assertIsNotNone(ibc_ini)
        ini_path = Path(ibc_ini)
        self.assertTrue(ini_path.exists(), f"IBC ini not found at {ini_path}")

        # Check OverrideTwsApiPort
        content = ini_path.read_text(encoding="utf-8")
        self.assertIn("OverrideTwsApiPort=4001", content)
        self.assertIn("AcceptIncomingConnectionAction=accept", content)
        self.assertIn("CommandServerPort=0", content)

    def test_live_read_config(self):
        """Verify config/live-read has port 4002 and matching IBC ini with OverrideTwsApiPort=4002."""
        sys_config = SystemConfig("config/live-read")
        port = sys_config.get("connection.port")
        self.assertEqual(port, 4002)

        ibc_enabled = sys_config.get("ibc.enabled")
        self.assertTrue(ibc_enabled)

        userid = sys_config.get("ibc.userid")
        self.assertEqual(userid, "jensengelke-bot2")

        ibc_ini = sys_config.get("ibc.ibcIni")
        self.assertIsNotNone(ibc_ini)
        ini_path = Path(ibc_ini)
        self.assertTrue(ini_path.exists(), f"IBC ini not found at {ini_path}")

        # Check OverrideTwsApiPort
        content = ini_path.read_text(encoding="utf-8")
        self.assertIn("OverrideTwsApiPort=4002", content)
        self.assertIn("AcceptIncomingConnectionAction=accept", content)
        self.assertIn("CommandServerPort=0", content)

    def test_live_read_bot_discovery(self):
        """Verify BotManager discovers telegram_notifier in config/live-read."""
        bm = BotManager("config/live-read")
        bots = bm.discover_bots()
        self.assertIn("telegram_notifier", bots)
        bot_cfg = bm.load_bot_config("telegram_notifier")
        self.assertEqual(bot_cfg.get("type"), "telegram")


if __name__ == "__main__":
    unittest.main()
