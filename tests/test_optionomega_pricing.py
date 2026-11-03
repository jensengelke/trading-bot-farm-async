"""
Unit tests for OptionOmega pricing, P/L, exit targets, premium filters, and slippage measurement.
"""

import unittest
from bots.optionomega.pricing import (
    calculate_exit_target,
    calculate_pnl,
    calculate_slippage,
    constrain_entry_limit,
    round_to_tick,
    validate_entry_premium_filters,
)


class TestOptionOmegaPricing(unittest.TestCase):

    def test_round_to_tick(self):
        self.assertEqual(round_to_tick(1.004, 0.01), 1.00)
        self.assertEqual(round_to_tick(1.006, 0.01), 1.01)
        self.assertEqual(round_to_tick(-1.005, 0.01), -1.01)
        self.assertEqual(round_to_tick(2.37, 0.05), 2.35)
        self.assertEqual(round_to_tick(2.38, 0.05), 2.40)
        with self.assertRaises(ValueError):
            round_to_tick(1.0, 0)

    def test_pnl_calculation(self):
        pts, dollars = calculate_pnl(-2.00, -1.00, multiplier=100.0, quantity=2)
        self.assertAlmostEqual(pts, 1.00)
        self.assertAlmostEqual(dollars, 200.00)

        pts, dollars = calculate_pnl(2.50, 1.50, multiplier=100.0, quantity=1)
        self.assertAlmostEqual(pts, -1.00)
        self.assertAlmostEqual(dollars, -100.00)

    def test_percent_targets_plan_examples(self):
        pt = calculate_exit_target(-2.00, "percent", 50, is_profit_target=True)
        self.assertAlmostEqual(pt, -1.00)

        sl = calculate_exit_target(-2.00, "percent", 100, is_profit_target=False)
        self.assertAlmostEqual(sl, -4.00)

        pt = calculate_exit_target(2.00, "percent", 50, is_profit_target=True)
        self.assertAlmostEqual(pt, 3.00)

        sl = calculate_exit_target(2.00, "percent", 50, is_profit_target=False)
        self.assertAlmostEqual(sl, 1.00)

    def test_fixed_premium_targets(self):
        pt = calculate_exit_target(-2.00, "fixed_premium", 0.50, is_profit_target=True)
        self.assertAlmostEqual(pt, -1.50)

        sl = calculate_exit_target(-2.00, "fixed_premium", 1.00, is_profit_target=False)
        self.assertAlmostEqual(sl, -3.00)

        pt = calculate_exit_target(2.00, "fixed_premium", 0.80, is_profit_target=True)
        self.assertAlmostEqual(pt, 2.80)

        sl = calculate_exit_target(2.00, "fixed_premium", 0.40, is_profit_target=False)
        self.assertAlmostEqual(sl, 1.60)

    def test_closing_premium_targets(self):
        pt = calculate_exit_target(-2.00, "closing_premium", 0.50, is_profit_target=True)
        self.assertAlmostEqual(pt, -0.50)

        sl = calculate_exit_target(-2.00, "closing_premium", 3.50, is_profit_target=False)
        self.assertAlmostEqual(sl, -3.50)

        pt = calculate_exit_target(2.00, "closing_premium", 3.00, is_profit_target=True)
        self.assertAlmostEqual(pt, 3.00)

        sl = calculate_exit_target(2.00, "closing_premium", 1.00, is_profit_target=False)
        self.assertAlmostEqual(sl, 1.00)

    def test_exit_target_validations(self):
        with self.assertRaises(ValueError):
            calculate_exit_target(0.0, "percent", 50, is_profit_target=True)

        with self.assertRaises(ValueError):
            calculate_exit_target(2.00, "percent", 110, is_profit_target=False)
        with self.assertRaises(ValueError):
            calculate_exit_target(2.00, "fixed_premium", 2.50, is_profit_target=False)

        with self.assertRaises(ValueError):
            calculate_exit_target(-2.00, "closing_premium", 3.00, is_profit_target=True)
        with self.assertRaises(ValueError):
            calculate_exit_target(2.00, "closing_premium", 1.50, is_profit_target=True)
        with self.assertRaises(ValueError):
            calculate_exit_target(2.00, "closing_premium", 2.50, is_profit_target=False)


    def test_premium_filters(self):
        with self.assertRaises(ValueError):
            validate_entry_premium_filters(-1.50, min_credit=1.00, oo_min=1.00)

        with self.assertRaises(ValueError):
            validate_entry_premium_filters(-1.50, min_credit=2.00, max_credit=1.00)
        with self.assertRaises(ValueError):
            validate_entry_premium_filters(1.50, min_debit=3.00, max_debit=1.00)
        with self.assertRaises(ValueError):
            validate_entry_premium_filters(1.50, oo_min=3.00, oo_max=1.00)

        ok, reason = validate_entry_premium_filters(0.0, min_credit=1.00)
        self.assertFalse(ok)
        self.assertIn("Zero-price", reason)

        ok, _ = validate_entry_premium_filters(-1.50, min_credit=1.00, max_credit=2.00)
        self.assertTrue(ok)
        ok, _ = validate_entry_premium_filters(-0.80, min_credit=1.00, max_credit=2.00)
        self.assertFalse(ok)
        ok, _ = validate_entry_premium_filters(-2.50, min_credit=1.00, max_credit=2.00)
        self.assertFalse(ok)

        ok, reason = validate_entry_premium_filters(1.50, min_credit=1.00, max_credit=2.00)
        self.assertFalse(ok)
        self.assertIn("Debit quote", reason)

        ok, _ = validate_entry_premium_filters(1.50, min_debit=1.00, max_debit=2.00)
        self.assertTrue(ok)
        ok, _ = validate_entry_premium_filters(0.50, min_debit=1.00, max_debit=2.00)
        self.assertFalse(ok)
        ok, _ = validate_entry_premium_filters(2.50, min_debit=1.00, max_debit=2.00)
        self.assertFalse(ok)

        ok, reason = validate_entry_premium_filters(-1.50, min_debit=1.00, max_debit=2.00)
        self.assertFalse(ok)
        self.assertIn("Credit quote", reason)

        ok, _ = validate_entry_premium_filters(-1.50, oo_min=1.00, oo_max=2.00)
        self.assertTrue(ok)
        ok, _ = validate_entry_premium_filters(1.50, oo_min=1.00, oo_max=2.00)
        self.assertTrue(ok)
        ok, _ = validate_entry_premium_filters(-0.50, oo_min=1.00, oo_max=2.00)
        self.assertFalse(ok)
        ok, _ = validate_entry_premium_filters(2.50, oo_min=1.00, oo_max=2.00)
        self.assertFalse(ok)

        ok, _ = validate_entry_premium_filters(-3.50)
        self.assertTrue(ok)
        ok, _ = validate_entry_premium_filters(3.50)
        self.assertTrue(ok)

    def test_constrain_entry_limit(self):
        limit = constrain_entry_limit(
            candidate_limit=2.05,
            anchor_mid=2.00,
            max_adverse_price_change=0.10,
            min_debit=1.00,
            max_debit=3.00,
        )
        self.assertAlmostEqual(limit, 2.05)

        limit = constrain_entry_limit(
            candidate_limit=2.15,
            anchor_mid=2.00,
            max_adverse_price_change=0.10,
            min_debit=1.00,
            max_debit=3.00,
        )
        self.assertAlmostEqual(limit, 2.10)

        limit = constrain_entry_limit(
            candidate_limit=-1.95,
            anchor_mid=-2.00,
            max_adverse_price_change=0.10,
            min_credit=1.00,
            max_credit=3.00,
        )
        self.assertAlmostEqual(limit, -1.95)

        with self.assertRaises(ValueError):
            constrain_entry_limit(
                candidate_limit=0.05,
                anchor_mid=-0.02,
                max_adverse_price_change=0.10,
            )

    def test_calculate_slippage(self):
        pts, dollars = calculate_slippage(2.00, 2.03, role="open", multiplier=100.0, quantity=1)
        self.assertAlmostEqual(pts, 0.03)
        self.assertAlmostEqual(dollars, 3.0)

        pts, dollars = calculate_slippage(2.00, 1.98, role="open", multiplier=100.0, quantity=2)
        self.assertAlmostEqual(pts, -0.02)
        self.assertAlmostEqual(dollars, -4.0)

        pts, dollars = calculate_slippage(1.00, 0.95, role="stoploss", multiplier=100.0, quantity=1)
        self.assertAlmostEqual(pts, 0.05)
        self.assertAlmostEqual(dollars, 5.0)

        pts, dollars = calculate_slippage(3.00, 3.02, role="takeprofit", multiplier=100.0, quantity=1)
        self.assertAlmostEqual(pts, -0.02)
        self.assertAlmostEqual(dollars, -2.0)


if __name__ == "__main__":
    unittest.main()

