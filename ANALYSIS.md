# FKK Bot Timeout Analysis

## Problem Summary
The FKK bot's `_find_spread_legs` method was called at 14:13:22 but didn't fail until 17:45:00 (3 hours 32 minutes later) with a "Socket disconnect" error.

## Timeline
- **14:13:20** - All conditions met, placing trade
- **14:13:22** - Started finding spread legs
- **17:45:00** - Error: Socket disconnect at line 495 (`reqSecDefOptParamsAsync`)

## Root Cause Analysis

### The Hanging Call
Line 495 in `_find_spread_legs`:
```python
chains = await self.ib.reqSecDefOptParamsAsync("SPX", "", "IND", 0)
```

This IB API call hung for over 3 hours without timing out. The likely causes:

1. **IB Gateway/TWS Restart**: At 17:45, there was a restart (mentioned by user). The connection was lost, causing the pending async call to finally fail.

2. **No Timeout on IB API Calls**: The `ib_async` library doesn't have built-in timeouts on API requests. If IB doesn't respond, the call waits indefinitely.

3. **Market Closure**: SPX options market closes at 15:00 Central (16:00 Eastern). The bot tried to get option chains at 14:13 Central (15:13 Eastern), which is after market close. IB may have stopped responding to this request.

### Why It Should Not Have Tried

Looking at the code flow:
1. `_check_tradeable_and_weekday` (line 217) checks if market is open
2. It correctly identified market hours as 08:30-15:00 Central
3. At 13:13 Central, it correctly said "SPX market is currently open"
4. **BUT** the execution time was 14:13 Eastern = 13:13 Central
5. By the time `_find_spread_legs` was called (14:13:22), the market was still open in Central time

**The real issue**: The bot doesn't re-check if the market is still open before placing the trade. Between checking conditions and placing the trade, time passes, and the market could close.

## Missing Safeguards

1. **No timeout on IB API calls** - Any IB API call can hang indefinitely
2. **No market hours check before trade placement** - Only checks at condition monitoring start
3. **No timeout on `_place_bull_put_spread`** - The entire trade placement has no timeout
4. **No timeout on `_find_spread_legs`** - Can hang forever waiting for IB response

## Recommended Fixes

### 1. Add Timeouts to All IB API Calls
Wrap all IB async calls with `asyncio.wait_for()`:

```python
try:
    chains = await asyncio.wait_for(
        self.ib.reqSecDefOptParamsAsync("SPX", "", "IND", 0),
        timeout=30  # 30 second timeout
    )
except asyncio.TimeoutError:
    self.logger.error("Timeout getting option chains")
    return None, None
```

### 2. Re-check Market Hours Before Trade Placement
Add a market hours check in `_place_bull_put_spread` before attempting to find spread legs.

### 3. Add Overall Timeout to Trade Placement
The entire `_place_bull_put_spread` operation should have a timeout (e.g., 5 minutes max).

### 4. Add Timeout to `_execute_trading_logic`
The condition monitoring has a 5-minute timeout, but the trade placement itself doesn't. Once conditions are met, the trade placement should also have a timeout.

## Implementation Priority

1. **HIGH**: Add timeouts to all IB API calls (prevents indefinite hangs)
2. **HIGH**: Add overall timeout to `_place_bull_put_spread` (prevents long-running operations)
3. **MEDIUM**: Re-check market hours before trade placement (prevents trading when market is closed)
4. **LOW**: Add more granular timeouts to individual operations
