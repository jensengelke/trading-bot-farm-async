# Bot Implementation Guide

## Lifecycle
The framework's bot_manager will invoke a set of lifecycle methods for each bot as the framework initializes and changes state.

1. `start()` - Called when the bot is started
2. `stop()` - Called when the bot is stopped

## Logging

The trading bot framework provides comprehensive logging functionality for both system-level and bot-level operations.

### Log File Structure

When you run the framework with a configuration (e.g., `python trading_bot_farm.py --config config/live`), logs are automatically organized in instance-specific directories:

```
logs/
└── live/                          # Instance name from config directory
    ├── system.log                 # System INFO and above
    ├── system-error.log           # System WARNING and above
    ├── system-trace.log           # System all levels (including DEBUG)
    ├── verify_stocks.log          # Bot INFO and above
    ├── verify_stocks-error.log    # Bot WARNING and above
    ├── verify_stocks-trace.log    # Bot all levels (including DEBUG)
    └── backup-2026-06-18-094513/  # Rotated logs from previous run
        ├── system.log
        ├── system-error.log
        └── ...
```

### Log Rotation

On each system start, existing log files are automatically moved into a timestamped backup directory (e.g., `logs/live/backup-2026-06-18-094513/`). This ensures you always have fresh logs for the current run while preserving historical logs.

### Using Logging in Your Bot

Every bot automatically has access to a logger via `self.logger`. The logger is configured with your bot's ID, so all log messages are automatically associated with your bot instance.

#### Example Bot Implementation

```python
from framework.bot_base import BotBase
from framework.decorators import trace_all_methods

@trace_all_methods
class Bot(BotBase):
    """Your bot implementation."""
    
    async def start(self) -> None:
        """Start the bot."""
        # Log at different levels
        self.logger.info("Bot starting")
        self.logger.debug("Debug information")
        
        try:
            # Your bot logic here
            result = await self.do_something()
            self.logger.info(f"Operation completed: {result}")
        except Exception as e:
            # Errors are automatically logged with full stack traces
            self.logger.error(f"Operation failed: {e}", exc_info=True)
            raise
    
    async def stop(self) -> None:
        """Stop the bot."""
        self.logger.info("Bot stopping")
```

### Log Levels

The framework uses standard Python logging levels:

- **DEBUG**: Detailed diagnostic information (only in trace logs)
- **INFO**: General informational messages
- **WARNING**: Warning messages for potentially problematic situations
- **ERROR**: Error messages for serious problems
- **CRITICAL**: Critical messages for very serious errors

### Method Tracing

The framework provides automatic method tracing via the `@trace_all_methods` decorator. When applied to a class, it automatically logs entry and exit of all public methods at DEBUG level.

```python
from framework.decorators import trace_all_methods

@trace_all_methods
class Bot(BotBase):
    # All public methods are automatically traced
    async def start(self) -> None:
        pass  # Entry and exit logged automatically
```

Trace logs include:
- Method name and class
- Arguments passed to the method
- Return values
- Exceptions raised

These traces appear in the `-trace.log` files and are invaluable for debugging.

### Best Practices

1. **Use appropriate log levels**: 
   - INFO for normal operations
   - WARNING for recoverable issues
   - ERROR for failures that need attention

2. **Include context in log messages**:
   ```python
   self.logger.info(f"Processing symbol {ticker} on {exchange}")
   ```

3. **Log exceptions with stack traces**:
   ```python
   except Exception as e:
       self.logger.error(f"Failed to process: {e}", exc_info=True)
   ```

4. **Use the trace decorator**: Apply `@trace_all_methods` to your bot class for automatic method tracing

5. **Don't log sensitive information**: Avoid logging passwords, API keys, or other secrets

### Accessing Logs

- **During development**: Check the trace logs for detailed debugging information
- **In production**: Monitor error logs for issues requiring attention
- **For analysis**: Use the standard logs for operational insights

All log files use UTF-8 encoding and include timestamps in the format `YYYY-MM-DD HH:MM:SS`.

