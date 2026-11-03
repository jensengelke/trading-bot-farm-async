"""
OptionOmega pricing, exit target calculations, premium filters, and slippage measurement.

Pricing Vocabulary & Units (Plan Section 2 & 3):
- Canonical spread: Leg actions describe opening the strategy.
  Open by BUYing this canonical BAG; close by SELLing the same BAG.
- E: Signed opening fill price in canonical BUY perspective (debit positive, credit negative).
- M: Signed current value of that same spread (not signed cashflow of closing transaction).
- Gross P/L per spread in price points: M - E
- Gross P/L in dollars: (M - E) * multiplier * filled_quantity (typically multiplier 100).
- Slippage is measured after the fact, never configured. Adverse slippage is positive.
"""

from decimal import Decimal, ROUND_HALF_UP
import math
from typing import Optional, Tuple


def round_to_tick(price: float, tick_size: float = 0.01) -> float:
    """Round price to the nearest tick size using standard half-up rounding."""
    if tick_size <= 0:
        raise ValueError(f"tick_size must be positive, got {tick_size}")
    d_price = Decimal(str(price))
    d_tick = Decimal(str(tick_size))
    ticks = (d_price / d_tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return float(ticks * d_tick)


def calculate_pnl(
    entry_price: float,
    current_mark: float,
    multiplier: float = 100.0,
    quantity: int = 1,
) -> Tuple[float, float]:
    """Calculate gross P/L in price points per spread and in total dollars."""
    pnl_points = current_mark - entry_price
    pnl_dollars = pnl_points * multiplier * quantity
    return pnl_points, pnl_dollars


def calculate_exit_target(
    entry_price: float,
    target_type: str,
    target_value: float,
    is_profit_target: bool,
    tick_size: float = 0.01,
) -> float:
    """
    Calculate the target signed mark M for profit target or stop loss.

    Target types:
      - 'percent': percentage p of opening premium magnitude abs(E).
      - 'fixed_premium': fixed price points x per spread.
      - 'closing_premium': positive closing premium magnitude c.
    """
    if target_value <= 0:
        raise ValueError(f"target_value must be positive, got {target_value}")

    mag_e = abs(entry_price)

    if target_type == "percent":
        if mag_e == 0:
            raise ValueError("Cannot calculate percentage exit target on zero entry price")
        delta = mag_e * (target_value / 100.0)
        target = (entry_price + delta) if is_profit_target else (entry_price - delta)

    elif target_type == "fixed_premium":
        target = (entry_price + target_value) if is_profit_target else (entry_price - target_value)

    elif target_type == "closing_premium":
        c = target_value
        if entry_price < 0:
            target = -c
        elif entry_price > 0:
            target = +c
        else:
            raise ValueError("Cannot set closing premium target on zero entry price")

        if is_profit_target and target <= entry_price:
            raise ValueError(
                f"Closing premium target {c} results in mark {target} which is not a profit over entry {entry_price}"
            )
        if not is_profit_target and target >= entry_price:
            raise ValueError(
                f"Closing premium target {c} results in mark {target} which is not a loss relative to entry {entry_price}"
            )

    else:
        raise ValueError(f"Unknown target_type: {target_type}")

    # For debit entries, option spread value cannot drop below 0
    if entry_price > 0 and not is_profit_target and target < 0:
        raise ValueError(
            f"Infeasible debit stop loss: target mark {target} is negative for debit entry {entry_price}"
        )

    if is_profit_target and target <= entry_price:
        raise ValueError(f"Profit target mark {target} must be greater than entry price {entry_price}")
    if not is_profit_target and target >= entry_price:
        raise ValueError(f"Stop loss mark {target} must be less than entry price {entry_price}")

    return round_to_tick(target, tick_size)

def validate_entry_premium_filters(
    quote_mid: float,
    min_credit: Optional[float] = None,
    max_credit: Optional[float] = None,
    min_debit: Optional[float] = None,
    max_debit: Optional[float] = None,
    oo_min: Optional[float] = None,
    oo_max: Optional[float] = None,
) -> Tuple[bool, str]:
    """
    Check if a quote mid price satisfies configured entry premium bounds.

    Bounds rules (Plan Section 3):
    1. If any explicit bound is non-null, use only explicit family. Both non-null is error.
    2. Credit quote (quote_mid < 0) checked against credit pair; debit quote (quote_mid > 0)
       against debit pair. Zero-price entries are rejected.
    3. If no explicit bound supplied, use OO-prefixed pair if present (magnitude check).
    4. If neither supplied, no filter applies.
    """
    has_explicit = any(b is not None for b in (min_credit, max_credit, min_debit, max_debit))
    has_oo = any(b is not None for b in (oo_min, oo_max))

    if has_explicit and has_oo:
        raise ValueError("Cannot supply both explicit entry premium bounds and OptionOmega bounds")

    if min_credit is not None and max_credit is not None and min_credit > max_credit:
        raise ValueError(f"min_credit ({min_credit}) cannot exceed max_credit ({max_credit})")
    if min_debit is not None and max_debit is not None and min_debit > max_debit:
        raise ValueError(f"min_debit ({min_debit}) cannot exceed max_debit ({max_debit})")
    if oo_min is not None and oo_max is not None and oo_min > oo_max:
        raise ValueError(f"oo_min ({oo_min}) cannot exceed oo_max ({oo_max})")

    if quote_mid == 0:
        return False, "Zero-price entry is rejected"

    if has_explicit:
        is_credit = quote_mid < 0
        mag = abs(quote_mid)

        if is_credit:
            has_credit_bounds = min_credit is not None or max_credit is not None
            if not has_credit_bounds:
                return False, f"Credit quote {quote_mid} not permitted when only debit bounds configured"
            if min_credit is not None and mag < min_credit:
                return False, f"Credit magnitude {mag:.4f} is below min_credit {min_credit}"
            if max_credit is not None and mag > max_credit:
                return False, f"Credit magnitude {mag:.4f} exceeds max_credit {max_credit}"
            return True, "Eligible credit"
        else:
            has_debit_bounds = min_debit is not None or max_debit is not None
            if not has_debit_bounds:
                return False, f"Debit quote {quote_mid} not permitted when only credit bounds configured"
            if min_debit is not None and mag < min_debit:
                return False, f"Debit magnitude {mag:.4f} is below min_debit {min_debit}"
            if max_debit is not None and mag > max_debit:
                return False, f"Debit magnitude {mag:.4f} exceeds max_debit {max_debit}"
            return True, "Eligible debit"

    if has_oo:
        mag = abs(quote_mid)
        if oo_min is not None and mag < oo_min:
            return False, f"Quote magnitude {mag:.4f} is below option_omega_min_entry_premium {oo_min}"
        if oo_max is not None and mag > oo_max:
            return False, f"Quote magnitude {mag:.4f} exceeds option_omega_max_entry_premium {oo_max}"
        return True, "Eligible under OptionOmega premium bounds"

    return True, "No premium filters configured"



def constrain_entry_limit(
    candidate_limit: float,
    anchor_mid: float,
    max_adverse_price_change: float,
    min_credit: Optional[float] = None,
    max_credit: Optional[float] = None,
    min_debit: Optional[float] = None,
    max_debit: Optional[float] = None,
    oo_min: Optional[float] = None,
    oo_max: Optional[float] = None,
    tick_size: float = 0.01,
) -> float:
    """Constrain entry limit order price during placement or chase."""
    if anchor_mid == 0:
        raise ValueError("Cannot constrain entry limit with zero anchor_mid")
    if max_adverse_price_change < 0:
        raise ValueError(f"max_adverse_price_change must be non-negative, got {max_adverse_price_change}")

    worst_limit = anchor_mid + max_adverse_price_change
    limit = min(candidate_limit, worst_limit)

    is_credit = anchor_mid < 0

    if is_credit and limit >= 0:
        raise ValueError(f"Chase limit {limit} crosses from credit into debit (anchor: {anchor_mid})")
    if not is_credit and limit <= 0:
        raise ValueError(f"Chase limit {limit} crosses from debit into credit (anchor: {anchor_mid})")

    has_explicit = any(b is not None for b in (min_credit, max_credit, min_debit, max_debit))
    has_oo = any(b is not None for b in (oo_min, oo_max))

    if has_explicit and has_oo:
        raise ValueError("Cannot supply both explicit entry premium bounds and OptionOmega bounds")

    if has_explicit:
        if is_credit:
            if max_credit is not None:
                limit = max(limit, -max_credit)
            if min_credit is not None:
                limit = min(limit, -min_credit)
        else:
            if min_debit is not None:
                limit = max(limit, min_debit)
            if max_debit is not None:
                limit = min(limit, max_debit)

    elif has_oo:
        mag = abs(limit)
        if oo_min is not None and mag < oo_min:
            limit = -oo_min if is_credit else oo_min
        if oo_max is not None and mag > oo_max:
            limit = -oo_max if is_credit else oo_max

    return round_to_tick(limit, tick_size)


def calculate_slippage(
    reference_price: float,
    fill_price: float,
    role: str,
    multiplier: float = 100.0,
    quantity: int = 1,
) -> Tuple[float, float]:
    """Measure slippage after execution. Positive indicates adverse execution."""
    if role == "open":
        slippage_points = fill_price - reference_price
    elif role in ("stoploss", "takeprofit", "close"):
        slippage_points = reference_price - fill_price
    else:
        raise ValueError(f"Unknown order role for slippage calculation: {role}")

    slippage_dollars = slippage_points * multiplier * quantity
    return slippage_points, slippage_dollars

