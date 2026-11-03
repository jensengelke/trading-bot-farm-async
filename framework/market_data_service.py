"""
Shared reference-counted market data service.

Provides real-time quotes, Greeks, spread valuation, and freshness validation
for option legs and underlying contracts.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional, Tuple
from ib_async import IB, Contract, Ticker


@dataclass
class QuoteSnapshot:
    contract: Contract
    bid: Optional[float] = None
    ask: Optional[float] = None
    last: Optional[float] = None
    mid: Optional[float] = None
    timestamp: Optional[datetime] = None
    delta: Optional[float] = None
    gamma: Optional[float] = None
    vega: Optional[float] = None
    theta: Optional[float] = None
    implied_vol: Optional[float] = None
    is_valid: bool = False
    invalidation_reason: str = ""


class MarketDataService:
    """Manages reference-counted market data subscriptions on a shared IB instance."""

    def __init__(self, ib: IB, logger: logging.Logger):
        self.ib = ib
        self.logger = logger
        self._ref_counts: Dict[int, int] = {}
        self._contracts: Dict[int, Contract] = {}
        self._tickers: Dict[int, Ticker] = {}

    def subscribe(self, contract: Contract, generic_tick_list: str = "100,101,106") -> Ticker:
        """Subscribe to market data for a contract (increments ref count)."""
        con_id = contract.conId or hash(contract.localSymbol or contract.symbol)
        if con_id in self._ref_counts:
            self._ref_counts[con_id] += 1
            return self._tickers[con_id]

        ticker = self.ib.reqMktData(contract, generic_tick_list, False, False)
        self._ref_counts[con_id] = 1
        self._contracts[con_id] = contract
        self._tickers[con_id] = ticker
        self.logger.debug(f"Subscribed market data for conId={con_id} ({contract.localSymbol or contract.symbol})")
        return ticker

    def unsubscribe(self, contract: Contract) -> None:
        """Decrement reference count; cancel subscription when count reaches zero."""
        con_id = contract.conId or hash(contract.localSymbol or contract.symbol)
        if con_id not in self._ref_counts:
            return

        self._ref_counts[con_id] -= 1
        if self._ref_counts[con_id] <= 0:
            del self._ref_counts[con_id]
            ticker = self._tickers.pop(con_id, None)
            self._contracts.pop(con_id, None)
            if ticker is not None:
                self.ib.cancelMktData(contract)
                self.logger.debug(f"Cancelled market data for conId={con_id}")

    def get_quote(
        self,
        contract: Contract,
        max_age_seconds: Optional[float] = None,
        max_bid_ask_width: Optional[float] = None,
    ) -> QuoteSnapshot:
        """Get current quote snapshot with freshness and bid/ask spread validation."""
        con_id = contract.conId or hash(contract.localSymbol or contract.symbol)
        ticker = self._tickers.get(con_id)
        snapshot = QuoteSnapshot(contract=contract)

        if ticker is None:
            snapshot.invalidation_reason = "Not subscribed to market data"
            return snapshot

        snapshot.bid = ticker.bid if ticker.bid and ticker.bid > 0 else None
        snapshot.ask = ticker.ask if ticker.ask and ticker.ask > 0 else None
        snapshot.last = ticker.last if ticker.last and ticker.last > 0 else None
        snapshot.timestamp = ticker.time or datetime.now(timezone.utc)

        if ticker.modelGreeks:
            snapshot.delta = ticker.modelGreeks.delta
            snapshot.gamma = ticker.modelGreeks.gamma
            snapshot.vega = ticker.modelGreeks.vega
            snapshot.theta = ticker.modelGreeks.theta
            snapshot.implied_vol = ticker.modelGreeks.impliedVol

        if snapshot.bid is None or snapshot.ask is None:
            snapshot.invalidation_reason = f"Missing bid ({snapshot.bid}) or ask ({snapshot.ask})"
            return snapshot

        if snapshot.bid > snapshot.ask:
            snapshot.invalidation_reason = f"Crossed market: bid {snapshot.bid} > ask {snapshot.ask}"
            return snapshot

        snapshot.mid = (snapshot.bid + snapshot.ask) / 2.0

        if max_bid_ask_width is not None:
            width = snapshot.ask - snapshot.bid
            if width > max_bid_ask_width:
                snapshot.invalidation_reason = f"Bid-ask width {width:.4f} exceeds limit {max_bid_ask_width}"
                return snapshot

        if max_age_seconds is not None and ticker.time:
            t = ticker.time if ticker.time.tzinfo else ticker.time.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - t).total_seconds()
            if age > max_age_seconds:
                snapshot.invalidation_reason = f"Quote is stale (age {age:.1f}s > {max_age_seconds}s)"
                return snapshot

        snapshot.is_valid = True
        return snapshot

    def get_canonical_spread_mark(
        self,
        legs: List[Tuple[Contract, int, str]],
        max_age_seconds: Optional[float] = 5.0,
        max_bid_ask_width: Optional[float] = None,
    ) -> Tuple[Optional[float], Optional[float], Optional[float], str]:
        """Compute canonical spread prices from individual leg quotes."""
        spread_mid = 0.0
        natural_bid = 0.0
        natural_ask = 0.0

        for contract, ratio, action in legs:
            snap = self.get_quote(contract, max_age_seconds, max_bid_ask_width)
            if not snap.is_valid:
                return None, None, None, f"Leg {contract.localSymbol or contract.symbol} invalid: {snap.invalidation_reason}"

            sign = 1 if action.upper() == "BUY" else -1
            spread_mid += snap.mid * ratio * sign

            if action.upper() == "BUY":
                natural_ask += snap.ask * ratio
                natural_bid += snap.bid * ratio
            else:
                natural_ask -= snap.bid * ratio
                natural_bid -= snap.ask * ratio

        return spread_mid, natural_bid, natural_ask, "OK"

