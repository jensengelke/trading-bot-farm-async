# FKK Bot Timeout Fix

## Problem
The FKK bot experienced a 3+ hour hang when calling `reqSecDefOptParamsAsync()` at 14:13:22, which didn't fail until 17:45:00 when IB Gateway was restarted. This prevented the bot from completing its daily execution cycle.

## Root Cause
The `ib_async` library does not have built-in timeouts on API requests. When IB Gateway stops responding (e.g., due to market closure, network issues, or pending restart), async calls wait indefinitely until the connection is severed.

## Solution Implemented
Added `asyncio.wait_for()` timeouts to all critical IB API calls in the FKK bot to prevent indefinite hangs:

### 1. Option Chain Retrieval (Line ~495)
**Timeout: 30 seconds**
```python
try:
    chains = await asyncio.wait_for(
        self.ib.reqSecDefOptParamsAsync("SPX", "", "IND", 0),
        timeout=30  # 30 second timeout
    )
except asyncio.TimeoutError:
    self.logger.error("Timeout getting option chains from IB (30 seconds exceeded)")
    return None, None
```

### 2. Option Contract Qualification - Individual (Line ~544)
**Timeout: 10 seconds per contract**
```python
try:
    short_put = (await asyncio.wait_for(
        self.ib.qualifyContractsAsync(short_put),
        timeout=10
    ))[0]
    long_put = (await asyncio.wait_for(
        self.ib.qualifyContractsAsync(long_put),
        timeout=10
    ))[0]
except asyncio.TimeoutError:
    self.logger.error("Timeout qualifying option contracts")
    return None, None
```

### 3. Option Contract Qualification - Batch (Line ~607)
**Timeout: 15 seconds per batch of 20 contracts**
```python
try:
    qualified_contracts = await asyncio.wait_for(
        self.ib.qualifyContractsAsync(*contracts_to_qualify),
        timeout=15  # 15 seconds for batch qualification
    )
except asyncio.TimeoutError:
    self.logger.error(f"Timeout qualifying option contracts in batch")
    offset += batch_size
    continue
```

## Benefits

1. **Prevents Indefinite Hangs**: Bot will fail fast (within seconds) instead of hanging for hours
2. **Better Error Messages**: Clear timeout errors in logs instead of generic "Socket disconnect"
3. **Faster Recovery**: Bot can retry or move to next day's cycle instead of being stuck
4. **Predictable Behavior**: Maximum execution time is now bounded and predictable

## Timeout Values Chosen

- **30 seconds** for option chain retrieval: This is a complex query that may take time
- **10 seconds** for individual contract qualification: Simple operation, should be fast
- **15 seconds** for batch qualification: Qualifying 20 contracts at once, slightly longer

These values are conservative and should work well under normal conditions while still catching hung requests quickly.

## Testing Recommendations

1. **Normal Operation**: Verify bot still works correctly during market hours
2. **Market Closure**: Test behavior when trying to get option chains after market close
3. **Network Issues**: Simulate slow/unresponsive IB Gateway to verify timeouts trigger
4. **IB Restart**: Verify graceful handling when IB Gateway restarts during execution

## Future Enhancements

Consider adding:
- Configurable timeout values in bot configuration
- Retry logic for transient failures
- Overall timeout for entire `_place_bull_put_spread()` operation
- Market hours re-check before trade placement

## Files Modified

- `bots/fkk/bot.py`: Added timeouts to 3 critical IB API calls
- `ANALYSIS.md`: Detailed root cause analysis
- `FKK_TIMEOUT_FIX.md`: This documentation

## Date
2026-06-27
