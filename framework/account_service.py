"""
Account Service

Provides helper functions for querying account balances, portfolio positions,
and open orders from IBKR via ib_async with timeouts and safe fallbacks.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple
from ib_async import IB, Trade


async def fetch_account_summary(
    ib: IB,
    account: str = "",
    timeout: float = 6.0,
    logger: Optional[logging.Logger] = None
) -> Dict[str, Dict[str, str]]:
    """
    Fetch account summary values (NetLiquidation, BuyingPower, TotalCashValue, etc.).
    
    Args:
        ib: Connected IB instance
        account: Account ID (optional, filter to specific account)
        timeout: Timeout in seconds for API request
        logger: Logger instance
        
    Returns:
        Dict mapping tag name to {'value': str, 'currency': str}
    """
    log = logger or logging.getLogger("account_service")
    summary: Dict[str, Dict[str, str]] = {}
    
    try:
        summary_list = await asyncio.wait_for(
            ib.accountSummaryAsync(account) if account else ib.accountSummaryAsync(),
            timeout=timeout
        )
        for av in summary_list:
            if not account or av.account == account:
                summary[av.tag] = {"value": av.value, "currency": av.currency}
    except Exception as e:
        log.debug(f"accountSummaryAsync failed or timed out: {e}; falling back to accountValues()")
        try:
            for av in ib.accountValues(account) if account else ib.accountValues():
                summary[av.tag] = {"value": av.value, "currency": av.currency}
        except Exception as fallback_e:
            log.warning(f"Failed to retrieve account values: {fallback_e}")
            
    return summary


async def fetch_portfolio_positions(
    ib: IB,
    account: str = "",
    timeout: float = 6.0,
    logger: Optional[logging.Logger] = None
) -> List[Any]:
    """
    Fetch active portfolio positions with non-zero quantity.
    
    Prefers ib.portfolio() (which contains marketPrice, unrealizedPNL, etc.)
    and falls back to reqPositionsAsync().
    
    Args:
        ib: Connected IB instance
        account: Account ID filter (optional)
        timeout: Timeout in seconds
        logger: Logger instance
        
    Returns:
        List of PortfolioItem or Position objects with non-zero position
    """
    log = logger or logging.getLogger("account_service")
    positions: List[Any] = []
    
    try:
        items = ib.portfolio(account) if account else ib.portfolio()
        if items:
            positions = list(items)
        else:
            pos_list = await asyncio.wait_for(
                ib.reqPositionsAsync(),
                timeout=timeout
            )
            if account:
                positions = [p for p in pos_list if p.account == account]
            else:
                positions = pos_list
    except Exception as e:
        log.debug(f"Error fetching portfolio positions: {e}; falling back to ib.positions()")
        try:
            positions = ib.positions(account) if account else ib.positions()
        except Exception as fallback_e:
            log.warning(f"Failed to retrieve positions: {fallback_e}")
            positions = []
            
    # Filter out 0 quantity positions
    return [p for p in positions if getattr(p, "position", 0) != 0]


async def fetch_open_orders(
    ib: IB,
    account: str = "",
    timeout: float = 6.0,
    logger: Optional[logging.Logger] = None
) -> List[Trade]:
    """
    Fetch active working open orders (excluding Filled, Cancelled, ApiCancelled).
    
    Args:
        ib: Connected IB instance
        account: Account ID filter (optional)
        timeout: Timeout in seconds
        logger: Logger instance
        
    Returns:
        List of active Trade objects
    """
    log = logger or logging.getLogger("account_service")
    open_trades: List[Trade] = []
    
    try:
        trades = await asyncio.wait_for(
            ib.reqAllOpenOrdersAsync(),
            timeout=timeout
        )
        open_trades = trades or ib.openTrades()
    except Exception as e:
        log.debug(f"reqAllOpenOrdersAsync failed or timed out: {e}; falling back to openTrades()")
        open_trades = ib.openTrades()
        
    filtered: List[Trade] = []
    for trade in open_trades:
        if account and trade.order.account and trade.order.account != account:
            continue
        status = trade.orderStatus.status if trade.orderStatus else ""
        if status not in ("Filled", "Cancelled", "ApiCancelled"):
            filtered.append(trade)
            
    return filtered


async def fetch_account_snapshot(
    ib: IB,
    account: str = "",
    timeout: float = 6.0,
    logger: Optional[logging.Logger] = None
) -> Tuple[Dict[str, Dict[str, str]], List[Any], List[Trade]]:
    """
    Fetch account balances, active portfolio positions, and working open orders in one call.
    
    Returns:
        (account_summary, active_positions, open_trades)
    """
    summary = await fetch_account_summary(ib, account=account, timeout=timeout, logger=logger)
    positions = await fetch_portfolio_positions(ib, account=account, timeout=timeout, logger=logger)
    orders = await fetch_open_orders(ib, account=account, timeout=timeout, logger=logger)
    return summary, positions, orders
