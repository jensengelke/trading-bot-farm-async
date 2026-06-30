# Request Tracking and Targeted Error Dispatching

## Overview

The Trading Bot Farm framework now includes a sophisticated request tracking system that associates IB API request IDs with the bot instances that originated them. This enables targeted error dispatching, ensuring that error events are delivered only to the bot that made the request, rather than being broadcast to all bots.

## Problem Statement

When multiple bots share a single IB connection, all bots receive all error events from the IB API. This creates several issues:

1. **Log Pollution**: Each bot logs errors for requests it didn't make
2. **Confusion**: Difficult to determine which bot actually caused an error
3. **Debugging Challenges**: Error logs are duplicated across multiple bot log files
4. **Resource Waste**: Unnecessary processing of irrelevant error events

### Example of the Problem

Before request tracking, if three bots were running and one made a request that generated an error, all three bots would log the same error:

```
# Bot 1 log
2026-06-30 13:52:51 - bot1 - WARNING - IB Error [reqId=328, code=200]: No security definition...

# Bot 2 log  
2026-06-30 13:52:51 - bot2 - WARNING - IB Error [reqId=328, code=200]: No security definition...

# Bot 3 log
2026-06-30 13:52:51 - bot3 - WARNING - IB Error [reqId=328, code=200]: No security definition...
```

## Solution Architecture

The framework now includes three key components:

### 1. RequestTracker

Maintains a mapping of request IDs to bot IDs:

```python
from framework.request_tracker import get_request_tracker

tracker = get_request_tracker()
tracker.register_request(req_id=328, bot_id="verify_stocks")
```

**Key Features:**
- Thread-safe request ID tracking
- Automatic cleanup when requests complete
- Statistics tracking for monitoring

### 2. ErrorDispatcher

Routes error events to the appropriate bot:

```python
from framework.request_tracker import get_error_dispatcher

dispatcher = get_error_dispatcher()
dispatcher.register_bot_handler(bot_id="verify_stocks", error_handler=my_error_handler)
dispatcher.dispatch_error(req_id=328, error_code=200, error_string="...", contract=None)
```

**Key Features:**
- Bot-specific error handler registration
- Automatic routing based on request ID
- Fallback to system logger for untracked requests

### 3. Integration with IBConnectionManager

The shared IB connection manager automatically registers a global error handler that uses the ErrorDispatcher:

```python
# In IBConnectionManager.connect()
self._ib.errorEvent += self._on_ib_error

def _on_ib_error(self, reqId, errorCode, errorString, contract):
    # Dispatch to the appropriate bot
    self._error_dispatcher.dispatch_error(reqId, errorCode, errorString, contract)
```

## How It Works

### Automatic Setup

When a bot is instantiated, the framework automatically:

1. Gets the global RequestTracker and ErrorDispatcher instances
2. Registers the bot's error handler with the ErrorDispatcher
3. Provides helper methods for tracking requests

```python
# In BotBase.__init__()
self._request_tracker = get_request_tracker()
self._error_dispatcher = get_error_dispatcher()
self._error_dispatcher.register_bot_handler(self.bot_id, self._on_ib_error)
```

### Error Handler Override

Bots can override the `_on_ib_error` method to provide custom error handling:

```python
class Bot(BotBase):
    def _on_ib_error(self, reqId: int, errorCode: int, errorString: str, contract) -> None:
        """Handle IB error events for this bot."""
        # Custom error handling logic
        if errorCode == 200:
            self.logger.warning(f"Contract not found: {contract}")
        else:
            self.logger.error(f"IB Error [{reqId}]: {errorString}")
```

### Request Tracking Usage

Bots should track requests when making IB API calls that return a request ID:

```python
# Example: Tracking a market data request
ticker = self.ib.reqMktData(contract, "", False, False)
if hasattr(ticker, 'reqId'):
    self.track_request(ticker.reqId)

# Example: Tracking a historical data request  
bars = await self.ib.reqHistoricalDataAsync(contract, ...)
# Note: Some async methods don't expose reqId, tracking happens internally
```

### Automatic Cleanup

When a bot is stopped, the framework automatically:

1. Clears all tracked requests for that bot
2. Unregisters the bot's error handler

```python
# In BotManager.stop_bot()
bot.cleanup()  # Clears requests and unregisters error handler
```

## Benefits

### 1. Clean Logs

Each bot only logs errors for its own requests:

```
# Bot 1 log (made the request)
2026-06-30 13:52:51 - bot1 - WARNING - IB Error [reqId=328, code=200]: No security definition...

# Bot 2 log (did not make the request)
(no error logged)

# Bot 3 log (did not make the request)
(no error logged)
```

### 2. Easier Debugging

When an error occurs, you immediately know which bot caused it by looking at the log file.

### 3. Custom Error Handling

Each bot can implement its own error handling logic without affecting other bots.

### 4. System-Level Errors

Errors for untracked requests (e.g., order-related errors, system errors) are logged to the system logger, ensuring nothing is lost.

## Implementation Guide

### For New Bots

New bots automatically get request tracking support by inheriting from `BotBase`. No additional code is required unless you want to:

1. **Override error handling:**
   ```python
   def _on_ib_error(self, reqId, errorCode, errorString, contract):
       # Custom logic
       pass
   ```

2. **Manually track requests** (usually not needed):
   ```python
   req_id = some_ib_api_call()
   self.track_request(req_id)
   ```

### For Existing Bots

Existing bots need minimal changes:

1. **Remove manual error event subscription:**
   ```python
   # REMOVE THIS:
   # self.ib.errorEvent += self._on_ib_error
   ```

2. **Keep the error handler method** (it will be called automatically):
   ```python
   # KEEP THIS:
   def _on_ib_error(self, reqId, errorCode, errorString, contract):
       # Your error handling logic
       pass
   ```

3. **Remove manual error event unsubscription:**
   ```python
   # REMOVE THIS:
   # self.ib.errorEvent -= self._on_ib_error
   ```

## Advanced Usage

### Manual Request Tracking

For IB API calls that return a request ID, you can manually track them:

```python
# Get request ID from IB API call
req_id = self.ib.reqSomeData(...)

# Track it
self.track_request(req_id)

# Later, when done with the request
self.untrack_request(req_id)
```

### Statistics

Get statistics about tracked requests:

```python
from framework.request_tracker import get_request_tracker

tracker = get_request_tracker()
stats = tracker.get_stats()
print(f"Total requests: {stats['total_requests']}")
print(f"Bots with requests: {stats['bots_with_requests']}")
```

### Clearing Bot Requests

Manually clear all requests for a specific bot:

```python
tracker.clear_bot_requests("my_bot_id")
```

## Technical Details

### Thread Safety

Both `RequestTracker` and `ErrorDispatcher` use threading locks to ensure thread-safe operation, even though the framework is async-based. This is necessary because IB API callbacks may occur on different threads.

### Request ID Lifecycle

1. **Registration**: When a bot makes an IB API call, it registers the request ID
2. **Error Dispatch**: If an error occurs, the ErrorDispatcher looks up the bot ID
3. **Cleanup**: When the bot stops or the request completes, the request ID is unregistered

### Untracked Requests

Some requests may not be tracked:

- **Order-related errors**: Order IDs are used as reqId, but may not be tracked
- **System errors**: Some errors have reqId=-1 or other special values
- **Legacy code**: Existing code that doesn't call `track_request()`

These errors are logged to the system logger with a note that they have no bot association.

## Migration Checklist

For existing bots, follow this checklist:

- [ ] Remove `self.ib.errorEvent += self._on_ib_error` from `start()` method
- [ ] Keep `_on_ib_error()` method (it will be called automatically)
- [ ] Remove `self.ib.errorEvent -= self._on_ib_error` from `stop()` method
- [ ] Test that errors are still logged correctly
- [ ] Verify that errors only appear in the correct bot's log file

## Troubleshooting

### Errors Not Being Logged

If errors are not being logged:

1. Check that the bot inherits from `BotBase`
2. Verify that `_on_ib_error()` method exists
3. Check system logs for untracked request errors

### Errors Appearing in Multiple Logs

If errors still appear in multiple bot logs:

1. Verify that manual error event subscriptions have been removed
2. Check that only one IB connection is being used (shared connection)
3. Review the request tracking implementation

### Missing Errors

If expected errors are not appearing:

1. Check the system log for untracked request errors
2. Verify that request IDs are being tracked correctly
3. Ensure the error handler is registered (check DEBUG logs)

## Future Enhancements

Potential future improvements:

1. **Automatic Request Tracking**: Intercept IB API calls to automatically track all request IDs
2. **Request Lifecycle Events**: Callbacks for request start, complete, error, timeout
3. **Request Metrics**: Track request duration, success rate, error rate per bot
4. **Request History**: Maintain a history of recent requests for debugging
5. **Request Correlation**: Link related requests (e.g., order placement and fills)
