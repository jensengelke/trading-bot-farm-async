"""
Option Spread Pairing and Risk Metrics.

Identifies credit spread positions (Bear Call Spreads, Bull Put Spreads)
from raw broker portfolio items and evaluates strike buffers and take-profit orders.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from ib_async import Trade, Contract


@dataclass
class OptionSpreadPosition:
    """Represents a paired option spread (e.g. Bear Call Spread or Bull Put Spread)."""
    spread_type: str                 # "BEAR_CALL_SPREAD" or "BULL_PUT_SPREAD"
    symbol: str                      # e.g. "DAX"
    expiration: str                  # e.g. "20261106"
    right: str                       # "C" or "P"
    quantity: int                    # Spread size (e.g. 4)
    short_leg: Any                   # PortfolioItem or Position
    long_leg: Any                    # PortfolioItem or Position
    short_strike: float
    long_strike: float
    multiplier: float = 5.0
    
    # Financial metrics
    short_avg_cost: float = 0.0
    long_avg_cost: float = 0.0
    unrealized_pnl: float = 0.0
    
    # Market metrics
    short_delta: Optional[float] = None
    underlying_price: Optional[float] = None
    
    # Protection audit
    has_take_profit: bool = False
    take_profit_order: Optional[Trade] = None
    take_profit_price: Optional[float] = None
    take_profit_action: Optional[str] = None
    take_profit_type: Optional[str] = None
    take_profit_qty: Optional[float] = None
    take_profit_tif: Optional[str] = None

    @property
    def wing_width(self) -> float:
        """Strike distance between short and long legs."""
        return abs(self.long_strike - self.short_strike)

    @property
    def short_strike_buffer_points(self) -> Optional[float]:
        """
        Buffer in index points from current underlying price to the short strike.
        Positive = safe (OTM), Negative = breached (ITM).
        """
        if self.underlying_price is None:
            return None
        if self.right == "C":
            # For calls, safe when underlying < short_strike
            return self.short_strike - self.underlying_price
        else:
            # For puts, safe when underlying > short_strike
            return self.underlying_price - self.short_strike

    @property
    def short_strike_buffer_percent(self) -> Optional[float]:
        """Buffer to short strike expressed as percentage of underlying price."""
        buf = self.short_strike_buffer_points
        if buf is None or not self.underlying_price:
            return None
        return (buf / self.underlying_price) * 100.0

    @property
    def long_strike_buffer_points(self) -> Optional[float]:
        """Buffer in index points to the protective long strike."""
        if self.underlying_price is None:
            return None
        if self.right == "C":
            return self.long_strike - self.underlying_price
        else:
            return self.underlying_price - self.long_strike

    def is_short_strike_tested(self, buffer_points: float = 0.0) -> bool:
        """
        Returns True if underlying price is testing the short strike
        (within buffer_points or beyond).
        """
        buf = self.short_strike_buffer_points
        if buf is None:
            return False
        return buf <= buffer_points

    def is_long_strike_tested(self) -> bool:
        """
        Returns True if underlying price has penetrated or reached the long strike.
        """
        buf = self.long_strike_buffer_points
        if buf is None:
            return False
        return buf <= 0.0



def _pair_legs_in_group(
    items: List[Any],
    exp: str,
    right: str,
    symbol: str
) -> List[OptionSpreadPosition]:
    """Pair shorts and longs within a single (expiration, right) group."""
    shorts = [it for it in items if getattr(it, "position", 0.0) < 0]
    longs = [it for it in items if getattr(it, "position", 0.0) > 0]
    if not shorts or not longs:
        return []

    shorts.sort(key=lambda it: float(getattr(it.contract, "strike", 0.0)))
    longs.sort(key=lambda it: float(getattr(it.contract, "strike", 0.0)))

    short_rem = {id(it): abs(float(it.position)) for it in shorts}
    long_rem = {id(it): float(it.position) for it in longs}
    spreads: List[OptionSpreadPosition] = []

    # Credit Call Spread: short strike < long strike
    # Credit Put Spread: short strike > long strike
    s_candidates = shorts if right == "C" else list(reversed(shorts))
    l_candidates = longs if right == "C" else list(reversed(longs))

    for s in s_candidates:
        s_id = id(s)
        s_strike = float(s.contract.strike)
        if short_rem[s_id] <= 0:
            continue

        for l in l_candidates:
            l_id = id(l)
            l_strike = float(l.contract.strike)
            if long_rem[l_id] <= 0:
                continue

            valid = (s_strike < l_strike) if right == "C" else (s_strike > l_strike)
            if valid:
                matched_qty = int(min(short_rem[s_id], long_rem[l_id]))
                if matched_qty > 0:
                    mult = float(getattr(s.contract, "multiplier", 5.0) or 5.0)
                    s_pnl = float(getattr(s, "unrealizedPNL", 0.0) or 0.0)
                    l_pnl = float(getattr(l, "unrealizedPNL", 0.0) or 0.0)
                    s_tot = abs(float(s.position))
                    l_tot = float(l.position)
                    
                    matched_pnl = (s_pnl * (matched_qty / s_tot)) + (l_pnl * (matched_qty / l_tot))
                    spread_type = "BEAR_CALL_SPREAD" if right == "C" else "BULL_PUT_SPREAD"

                    spreads.append(OptionSpreadPosition(
                        spread_type=spread_type,
                        symbol=symbol,
                        expiration=exp,
                        right=right,
                        quantity=matched_qty,
                        short_leg=s,
                        long_leg=l,
                        short_strike=s_strike,
                        long_strike=l_strike,
                        multiplier=mult,
                        short_avg_cost=float(getattr(s, "avgCost", 0.0) or getattr(s, "averageCost", 0.0)),
                        long_avg_cost=float(getattr(l, "avgCost", 0.0) or getattr(l, "averageCost", 0.0)),
                        unrealized_pnl=matched_pnl
                    ))
                    short_rem[s_id] -= matched_qty
                    long_rem[l_id] -= matched_qty

    return spreads



def identify_spread_positions(
    portfolio_items: List[Any],
    symbol: str = "DAX"
) -> List[OptionSpreadPosition]:
    """
    Identifies Bear Call Spreads and Bull Put Spreads from a list of portfolio items.
    
    Invariants:
    - Bear Call Spread: Short lower call, long higher call (|short.pos| == long.pos).
    - Bull Put Spread: Short higher put, long lower put (|short.pos| == long.pos).
    """
    opt_items = []
    for item in portfolio_items:
        c = getattr(item, "contract", None)
        if not c:
            continue
        c_symbol = getattr(c, "symbol", "")
        sec_type = getattr(c, "secType", "")
        qty = getattr(item, "position", 0.0)
        if c_symbol == symbol and sec_type == "OPT" and qty != 0:
            opt_items.append(item)

    groups: Dict[Tuple[str, str], List[Any]] = {}
    for item in opt_items:
        c = item.contract
        exp = str(getattr(c, "lastTradeDateOrContractMonth", "") or "")
        right = str(getattr(c, "right", "") or "").upper()
        key = (exp, right)
        if key not in groups:
            groups[key] = []
        groups[key].append(item)

    spreads: List[OptionSpreadPosition] = []
    for (exp, right), items in groups.items():
        matched = _pair_legs_in_group(items, exp, right, symbol)
        spreads.extend(matched)

    spreads.sort(key=lambda sp: (sp.expiration, sp.right, sp.short_strike))
    return spreads


def audit_take_profit_orders(
    spreads: List[OptionSpreadPosition],
    open_trades: List[Trade]
) -> None:
    """
    Checks open working orders to verify whether a Take-Profit or closing order exists
    for each identified spread.
    
    Handles:
    - Combo BAG orders containing the spread's legs. In TWS, right-clicking an existing
      position and selecting "Close" creates an order with action SELL and negative limit price
      (e.g. SELL 5x @ -3.00), or action BUY. Both are recognized as closing orders.
    - Single-leg closing orders on the short leg (e.g. BUY to close).
    """
    for spread in spreads:
        s_con_id = getattr(spread.short_leg.contract, "conId", 0)
        l_con_id = getattr(spread.long_leg.contract, "conId", 0)

        for trade in open_trades:
            order = trade.order
            contract = trade.contract
            status = trade.orderStatus.status if trade.orderStatus else ""
            if status in ("Filled", "Cancelled", "ApiCancelled", "Inactive"):
                continue

            # Combo BAG order
            if contract.secType == "BAG" and getattr(contract, "comboLegs", None):
                leg_con_ids = {leg.conId for leg in contract.comboLegs}
                if s_con_id in leg_con_ids and l_con_id in leg_con_ids:
                    spread.has_take_profit = True
                    spread.take_profit_order = trade
                    spread.take_profit_price = order.lmtPrice if order.lmtPrice not in (None, 0.0) else order.auxPrice
                    spread.take_profit_action = order.action
                    spread.take_profit_type = order.orderType
                    spread.take_profit_qty = order.totalQuantity
                    spread.take_profit_tif = order.tif
                    break

            # Single-leg closing order on short leg
            if contract.conId == s_con_id and order.action == "BUY":
                spread.has_take_profit = True
                spread.take_profit_order = trade
                spread.take_profit_price = order.lmtPrice if order.lmtPrice not in (None, 0.0) else order.auxPrice
                spread.take_profit_action = order.action
                spread.take_profit_type = order.orderType
                spread.take_profit_qty = order.totalQuantity
                spread.take_profit_tif = order.tif
                break


def is_dax_trade(trade: Trade, symbol: str = "DAX") -> bool:
    """Check if an open trade belongs to DAX index, DAX options, or a DAX combo."""
    c = trade.contract
    return getattr(c, "symbol", "") == symbol


