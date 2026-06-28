"""
Option Utilities

Shared utility functions for option selection and analysis.
"""

import asyncio
from typing import List, Optional, Tuple
from ib_async import IB, Option, Contract
import logging


async def find_option_by_delta(
    ib: IB,
    logger: logging.Logger,
    strikes: List[float],
    symbol: str,
    expiration: str,
    right: str,
    target_delta: float,
    batch_size: int = 20
) -> Optional[float]:
    """
    Find the strike with delta closest to target using actual option greeks from IB.
    
    Processes strikes in batches, stopping early if a delta smaller than target is found.
    This ensures we find the optimal strike even if it's beyond the first batch.
    
    For puts, delta is negative. This function uses abs() to compare absolute delta values,
    so you should pass target_delta as a positive value (e.g., 0.35 for a put with -0.35 delta).
    
    Args:
        ib: Connected IB instance
        logger: Logger instance for logging
        strikes: List of available strikes (should be sorted appropriately for the option type)
        symbol: Underlying symbol (e.g., "SPX")
        expiration: Option expiration date (format: YYYYMMDD)
        right: Option right ("C" for call, "P" for put)
        target_delta: Target delta value as absolute value (e.g., 0.35)
        batch_size: Number of strikes to process per batch (default: 20)
        
    Returns:
        Strike with delta closest to target, or None if not found
        
    Example:
        # Find put with delta closest to -0.35 (pass 0.35 as target)
        strike = await find_option_by_delta(
            ib, logger, strikes, "SPX", "20260628", "P", 0.35
        )
    """
    try:
        logger.info(f"Finding strike with delta closest to {target_delta} (target absolute value)")
        
        best_strike = None
        best_delta = None
        best_diff = float('inf')
        
        offset = 0
        
        # Process strikes in batches until we find a delta smaller than target or run out of strikes
        while offset < len(strikes):
            batch_strikes = strikes[offset:offset + batch_size]
            
            if not batch_strikes:
                break
            
            logger.debug(f"Processing batch: strikes {offset} to {offset + len(batch_strikes) - 1}")
            
            # Create option contracts for this batch
            option_contracts = []
            for strike in batch_strikes:
                option = Option(symbol, expiration, strike, right, "SMART", currency="USD")
                option_contracts.append((strike, option))
            
            # Qualify all contracts in batch
            contracts_to_qualify = [opt for _, opt in option_contracts]
            
            try:
                qualified_contracts = await asyncio.wait_for(
                    ib.qualifyContractsAsync(*contracts_to_qualify),
                    timeout=15  # 15 seconds for batch qualification
                )
            except asyncio.TimeoutError:
                logger.error(f"Timeout qualifying option contracts in batch")
                offset += batch_size
                continue
            except Exception as e:
                logger.error(f"Error qualifying option contracts in batch: {e}", exc_info=True)
                offset += batch_size
                continue
            
            # Request market data with greeks for each option
            tickers = []
            for i, qualified_contract in enumerate(qualified_contracts):
                if qualified_contract and isinstance(qualified_contract, Contract):
                    # Request market data with greeks (genericTickList "106" requests option greeks)
                    ticker = ib.reqMktData(qualified_contract, "106", False, False)
                    tickers.append((batch_strikes[i], ticker))
                else:
                    logger.warning(f"Failed to qualify contract for strike {batch_strikes[i]}")
            
            # Wait for market data to populate
            await asyncio.sleep(3)
            
            # Track if we should stop (found delta smaller than target)
            should_stop = False
            
            # Find the strike with delta closest to target in this batch
            for strike, ticker in tickers:
                # Check if we have model greeks and delta is a valid number
                if (ticker.modelGreeks and 
                    ticker.modelGreeks.delta is not None and 
                    isinstance(ticker.modelGreeks.delta, (int, float))):
                    
                    delta = ticker.modelGreeks.delta
                    
                    # For puts, delta is negative. We want absolute value for comparison
                    abs_delta = abs(delta)
                    diff = abs(abs_delta - target_delta)
                    
                    logger.debug(f"Strike {strike}: delta={delta:.4f}, abs_delta={abs_delta:.4f}, diff={diff:.4f}")
                    
                    # Update best if this is closer
                    if diff < best_diff:
                        best_diff = diff
                        best_strike = strike
                        best_delta = delta
                    
                    # Stop if we found a delta smaller than target (we've gone too far OTM)
                    if abs_delta < target_delta:
                        logger.info(f"Found delta {abs_delta:.4f} < target {target_delta:.4f} at strike {strike}, stopping search")
                        should_stop = True
                        break
                else:
                    logger.debug(f"Strike {strike}: No greeks available")
            
            # Cancel all market data subscriptions for this batch
            for _, ticker in tickers:
                ib.cancelMktData(ticker.contract)
            
            # Stop if we found a delta smaller than target
            if should_stop:
                break
            
            # Move to next batch
            offset += batch_size
        
        if best_strike is not None:
            logger.info(f"Selected strike {best_strike} with delta={best_delta:.4f} (abs={abs(best_delta):.4f}, target={target_delta:.4f}, diff={best_diff:.4f})")
            return best_strike
        else:
            logger.error("No suitable strike found with valid greeks")
            return None
        
    except Exception as e:
        logger.error(f"Error finding strike by delta: {e}", exc_info=True)
        return None
