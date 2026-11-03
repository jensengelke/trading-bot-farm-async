"""
Unit tests for OptionOmega configuration parsing, validation, and settings report.
"""

from pathlib import Path
import unittest
import yaml

from bots.optionomega.config import OptionOmegaBotConfig
from bots.optionomega.settings_report import analyze_optionomega_settings


class TestOptionOmegaConfig(unittest.TestCase):

    def setUp(self):
        fixture_path = Path("docs/fixtures/optionomega_calendar_example.yaml")
        with open(fixture_path, "r", encoding="utf-8") as f:
            self.valid_yaml_dict = yaml.safe_load(f)

    def test_valid_calendar_fixture(self):
        config = OptionOmegaBotConfig(**self.valid_yaml_dict)
        self.assertEqual(config.name, "SPY illustrative calendar")
        self.assertEqual(len(config.legs), 2)
        self.assertEqual(config.legs[0].id, "front")
        self.assertEqual(config.legs[1].strike.parent, "front")
        self.assertTrue(config.exit.close_full_position_before_first_leg_expires)
        self.assertEqual(config.exit.close_full_position_before_first_leg_expires_minutes, 20)

    def test_rejects_backtest_only_and_unknown_keys(self):
        invalid_dict = dict(self.valid_yaml_dict)
        invalid_dict["backtest_only"] = {"enabled": True}
        with self.assertRaises(ValueError):
            OptionOmegaBotConfig(**invalid_dict)

        invalid_dict = dict(self.valid_yaml_dict)
        invalid_dict["entry"] = dict(invalid_dict["entry"])
        invalid_dict["entry"]["slippage"] = 0.05
        with self.assertRaises(ValueError):
            OptionOmegaBotConfig(**invalid_dict)

    def test_rejects_both_explicit_and_oo_premium_bounds(self):
        d = dict(self.valid_yaml_dict)
        d["entry"] = dict(d["entry"])
        d["entry"]["min_debit_for_open"] = 1.00
        d["entry"]["option_omega_min_entry_premium"] = 1.00
        with self.assertRaises(ValueError):
            OptionOmegaBotConfig(**d)

    def test_duplicate_leg_ids(self):
        d = dict(self.valid_yaml_dict)
        d["legs"] = [
            dict(d["legs"][0], id="leg1"),
            dict(d["legs"][1], id="leg1"),
        ]
        with self.assertRaises(ValueError):
            OptionOmegaBotConfig(**d)

    def test_nonexistent_parent_leg(self):
        d = dict(self.valid_yaml_dict)
        d["legs"] = [
            dict(d["legs"][0], id="front"),
            dict(d["legs"][1], id="back", strike={"type": "offset", "parent": "ghost", "value": 0}),
        ]
        with self.assertRaises(ValueError):
            OptionOmegaBotConfig(**d)

    def test_self_referencing_parent_leg(self):
        d = dict(self.valid_yaml_dict)
        d["legs"] = [
            dict(d["legs"][0], id="front", strike={"type": "offset", "parent": "front", "value": 0}),
        ]
        with self.assertRaises(ValueError):
            OptionOmegaBotConfig(**d)

    def test_cyclic_parent_legs(self):
        d = dict(self.valid_yaml_dict)
        d["legs"] = [
            dict(d["legs"][0], id="leg1", strike={"type": "offset", "parent": "leg2", "value": 0}),
            dict(d["legs"][1], id="leg2", strike={"type": "offset", "parent": "leg1", "value": 0}),
        ]
        with self.assertRaises(ValueError):
            OptionOmegaBotConfig(**d)

    def test_stop_limit_requires_offset(self):
        d = dict(self.valid_yaml_dict)
        d["stop_loss"] = {
            "type": "percent",
            "value": 50,
            "order_type": "stop_limit",
            "stop_limit_offset": None,
        }
        with self.assertRaises(ValueError):
            OptionOmegaBotConfig(**d)

        # Providing offset is valid
        d["stop_loss"]["stop_limit_offset"] = 0.05
        cfg = OptionOmegaBotConfig(**d)
        self.assertEqual(cfg.stop_loss.order_type, "stop_limit")
        self.assertEqual(cfg.stop_loss.stop_limit_offset, 0.05)

    def test_first_expiry_policy_false_allows_null_minutes(self):
        d = dict(self.valid_yaml_dict)
        d["exit"] = {
            "close_full_position_before_first_leg_expires": False,
            "close_full_position_before_first_leg_expires_minutes": None,
            "managed_exits": [],
            "monitoring": {"interval_seconds": 60, "align_to_minute": True},
            "execution": None,
        }
        cfg = OptionOmegaBotConfig(**d)
        self.assertFalse(cfg.exit.close_full_position_before_first_leg_expires)
        self.assertIsNone(cfg.exit.close_full_position_before_first_leg_expires_minutes)

    def test_settings_report(self):
        raw = {
            "ticker": "SPY",
            "start_date": "2023-01-01",
            "end_date": "2023-12-31",
            "entry_slippage": 0.05,
            "margin_allocation": 0.20,
            "dix_gex": True,
            "custom_unknown_key": 123,
        }
        report = analyze_optionomega_settings(raw)
        self.assertEqual(len(report.mapped), 1)
        self.assertEqual(report.mapped[0].name, "ticker")
        self.assertEqual(len(report.ignored), 3)  # start_date, end_date, entry_slippage
        self.assertEqual(len(report.deferred), 1)  # margin_allocation
        self.assertEqual(len(report.unsupported), 1)  # dix_gex
        self.assertEqual(report.unknown, ["custom_unknown_key"])
        self.assertFalse(report.is_valid_live_config)


if __name__ == "__main__":
    unittest.main()
