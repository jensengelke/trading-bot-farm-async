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
    primary_strikes: List[float],
    alternate_strikes: List[float],
    symbol: str,
    expiration: str,
    right: str,
    target_delta: float,
    current_price: float,
    batch_size: int = 20
) -> Optional[float]:
    """
    Find the strike with delta closest to target using actual option greeks from IB.
    
    Searches primary strikes first (e.g., OTM strikes), then checks alternate strikes
    (e.g., ITM strikes) if the best match is the first element, indicating we may be
    searching in the wrong direction.
    
    For puts, delta is negative. This function uses abs() to compare absolute delta values,
    so you should pass target_delta as a positive value (e.g., 0.35 for a put with -0.35 delta).
    
    Args:
        ib: Connected IB instance
        logger: Logger instance for logging
        primary_strikes: Primary list of strikes to search (sorted appropriately)
        alternate_strikes: Alternate list of strikes to search if needed (opposite direction)
        symbol: Underlying symbol (e.g., "SPX")
        expiration: Option expiration date (format: YYYYMMDD)
        right: Option right ("C" for call, "P" for put)
        target_delta: Target delta value as absolute value (e.g., 0.35)
        current_price: Current price of the underlying
        batch_size: Number of strikes to process per batch (default: 20)
        
    Returns:
        Strike with delta closest to target, or None if not found
        
    Example:
        # Find call with delta closest to 0.65
        strike = await find_option_by_delta(
            ib, logger, primary_strikes, alternate_strikes, "SPX", "20260714", "C", 0.65, 7490.0
        )
    """
    try:
        logger.info(f"Finding strike with delta closest to {target_delta} (target absolute value, current price: {current_price:.2f})")
        
        best_strike = None
        best_delta = None
        best_diff = float('inf')
        best_strike_index = None
        
        offset = 0
        
        # Process primary strikes in batches until we find a delta smaller than target or run out of strikes
        while offset < len(primary_strikes):
            batch_strikes = primary_strikes[offset:offset + batch_size]
            
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
            for i, (strike, ticker) in enumerate(tickers):
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
                        best_strike_index = offset + i
                    
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
        
        # Check if the best strike is the first element - this might indicate we need to search the alternate direction
        if best_strike is not None and best_strike_index == 0 and len(alternate_strikes) > 0:
            logger.info(f"Best strike {best_strike} is the first element (index 0). Searching alternate strikes (opposite side of current price)...")
            
            # The first strike was the best, which suggests we might be looking in the wrong direction
            # This can happen with ITM options where delta > 0.5
            # Search the alternate strikes (e.g., ITM strikes if we were searching OTM)
            
            alternate_batch_size = min(batch_size, 10)  # Check fewer strikes in alternate direction
            
            logger.info(f"Searching up to {alternate_batch_size} alternate strikes...")
            
            # Process one batch of alternate strikes
            alternate_batch_strikes = alternate_strikes[:alternate_batch_size]
            
            if alternate_batch_strikes:
                # Create option contracts for alternate batch
                option_contracts = []
                for strike in alternate_batch_strikes:
                    option = Option(symbol, expiration, strike, right, "SMART", currency="USD")
                    option_contracts.append((strike, option))
                
                # Qualify all contracts in batch
                contracts_to_qualify = [opt for _, opt in option_contracts]
                
                try:
                    qualified_contracts = await asyncio.wait_for(
                        ib.qualifyContractsAsync(*contracts_to_qualify),
                        timeout=15
                    )
                    
                    # Request market data with greeks for each option
                    tickers = []
                    for i, qualified_contract in enumerate(qualified_contracts):
                        if qualified_contract and isinstance(qualified_contract, Contract):
                            ticker = ib.reqMktData(qualified_contract, "106", False, False)
                            tickers.append((alternate_batch_strikes[i], ticker))
                        else:
                            logger.warning(f"Failed to qualify contract for strike {alternate_batch_strikes[i]}")
                    
                    # Wait for market data to populate
                    await asyncio.sleep(3)
                    
                    # Check if any of these strikes are better
                    for strike, ticker in tickers:
                        if (ticker.modelGreeks and 
                            ticker.modelGreeks.delta is not None and 
                            isinstance(ticker.modelGreeks.delta, (int, float))):
                            
                            delta = ticker.modelGreeks.delta
                            abs_delta = abs(delta)
                            diff = abs(abs_delta - target_delta)
                            
                            logger.debug(f"Alternate search - Strike {strike}: delta={delta:.4f}, abs_delta={abs_delta:.4f}, diff={diff:.4f}")
                            
                            # Update best if this is closer
                            if diff < best_diff:
                                logger.info(f"Found better strike in alternate direction: {strike} (delta={delta:.4f}, diff={diff:.4f} vs previous diff={best_diff:.4f})")
                                best_diff = diff
                                best_strike = strike
                                best_delta = delta
                    
                    # Cancel all market data subscriptions for alternate batch
                    for _, ticker in tickers:
                        ib.cancelMktData(ticker.contract)
                        
                except Exception as e:
                    logger.warning(f"Error searching alternate strikes: {e}", exc_info=True)
        
        if best_strike is not None:
            logger.info(f"Selected strike {best_strike} with delta={best_delta:.4f} (abs={abs(best_delta):.4f}, target={target_delta:.4f}, diff={best_diff:.4f})")
            return best_strike
        else:
            logger.error("No suitable strike found with valid greeks")
            return None
        
    except Exception as e:
        logger.error(f"Error finding strike by delta: {e}", exc_info=True)
        return None
