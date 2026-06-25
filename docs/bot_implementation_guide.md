# Bot Implementation Guide

## Lifecycle
The framework's bot_manager will invoke a set of lifecycle methods for each bot as the framework initializes and changes state.

1. `start()` - Called when the bot is started
2. `stop()` - Called when the bot is stopped

## Configuration Schema

**IMPORTANT**: Every bot MUST define a Pydantic configuration schema to validate its configuration at startup. This ensures type safety and catches configuration errors before trading begins.

### Creating a Configuration Schema

Each bot type should have a `config.py` file in its directory (e.g., `bots/my_bot/config.py`) that defines a Pydantic model for validating the bot's configuration.

#### Example: Simple Bot Configuration

```python
"""
My Bot Configuration Schema
"""

from pydantic import BaseModel, Field, field_validator


class MyBotConfig(BaseModel):
    """Configuration schema for My Bot."""
    
    type: str = Field(..., description="Bot type identifier (must be 'my_bot')")
    
    symbol: str = Field(..., description="Trading symbol")
    
    quantity: int = Field(
        default=1,
        ge=1,
        le=100,
        description="Number of contracts to trade (1-100)"
    )
    
    @field_validator('type')
    @classmethod
    def validate_type(cls, v: str) -> str:
        """Validate that type matches bot type."""
        if v != 'my_bot':
            raise ValueError(f"Bot type must be 'my_bot', got '{v}'")
        return v
    
    model_config = {
        "extra": "forbid",  # Disallow extra fields
        "str_strip_whitespace": True,  # Strip whitespace from strings
    }
```

### Using the Configuration Schema in Your Bot

In your bot's `__init__` method, validate the configuration using the Pydantic model:

```python
from framework.bot_base import BotBase
from framework.decorators import trace_all_methods
from typing import Any, Dict

# Import bot config schema
try:
    from bots.my_bot.config import MyBotConfig
except ImportError:
    from .config import MyBotConfig


@trace_all_methods
class Bot(BotBase):
    """My bot implementation."""
    
    def __init__(self, bot_id: str, config: Dict[str, Any], system_config: Any, ib_connection_manager):
        """
        Initialize the bot.
        
        Args:
            bot_id: Unique identifier for this bot instance
            config: Bot-specific configuration (raw dict)
            system_config: System-wide configuration
            ib_connection_manager: Shared IB connection manager
        """
        super().__init__(bot_id, config, system_config, ib_connection_manager)
        
        # Validate configuration using Pydantic model
        try:
            self.validated_config = MyBotConfig(**config)
            self.logger.info("Configuration validated successfully")
        except Exception as e:
            self.logger.error(f"Configuration validation failed: {e}", exc_info=True)
            raise ValueError(f"Invalid configuration for bot {bot_id}: {e}") from e
```

### Configuration Validation Benefits

1. **Type Safety**: Ensures configuration values are the correct type
2. **Range Validation**: Validates numeric ranges (e.g., `ge=1, le=100`)
3. **Required Fields**: Ensures required fields are present
4. **Custom Validation**: Allows custom validation logic via `@field_validator`
5. **Early Failure**: Catches errors at startup, not during trading
6. **Documentation**: Schema serves as documentation for configuration options
7. **IDE Support**: Provides autocomplete and type checking in IDEs

### Advanced Configuration Examples

#### Nested Configuration

```python
from pydantic import BaseModel, Field
from typing import List


class SymbolConfig(BaseModel):
    """Configuration for a single symbol."""
    ticker: str = Field(..., description="Symbol ticker")
    exchange: str = Field(default="SMART", description="Exchange")
    currency: str = Field(default="USD", description="Currency")


class MyBotConfig(BaseModel):
    """Configuration schema for My Bot."""
    type: str = Field(..., description="Bot type identifier")
    symbols: List[SymbolConfig] = Field(..., min_length=1, description="List of symbols")
```

#### Literal Types (Enum-like)

```python
from typing import Literal
from pydantic import BaseModel, Field


class MyBotConfig(BaseModel):
    """Configuration schema for My Bot."""
    type: str = Field(..., description="Bot type identifier")
    
    security_type: Literal["stock", "option", "future"] = Field(
        default="stock",
        description="Type of security"
    )
```

#### Custom Validators

```python
from pydantic import BaseModel, Field, field_validator


class MyBotConfig(BaseModel):
    """Configuration schema for My Bot."""
    type: str = Field(..., description="Bot type identifier")
    
    execution_time: str = Field(
        default="14:13",
        description="Execution time in HH:MM format"
    )
    
    @field_validator('execution_time')
    @classmethod
    def validate_execution_time(cls, v: str) -> str:
        """Validate execution time format (HH:MM)."""
        try:
            parts = v.split(':')
            if len(parts) != 2:
                raise ValueError("Must be in HH:MM format")
            
            hour = int(parts[0])
            minute = int(parts[1])
            
            if not (0 <= hour <= 23):
                raise ValueError("Hour must be 0-23")
            if not (0 <= minute <= 59):
                raise ValueError("Minute must be 0-59")
            
            return v
        except (ValueError, AttributeError) as e:
            raise ValueError(f"Invalid execution_time format '{v}': must be HH:MM") from e
```

### Configuration File Structure

Each bot instance has a YAML configuration file (e.g., `config/live/my_bot_instance.yaml`):

```yaml
# Bot type (must match the bot directory name)
type: my_bot

# Bot-specific configuration
symbol: SPX
quantity: 2
execution_time: "14:30"
```

The framework loads this YAML file and passes it to your bot's `__init__` method, where it's validated against your Pydantic schema.

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
from typing import Any, Dict

@trace_all_methods
class Bot(BotBase):
    """Your bot implementation."""
    
    def __init__(self, bot_id: str, config: Dict[str, Any], system_config: Any, ib_connection_manager):
        """
        Initialize the bot.
        
        Args:
            bot_id: Unique identifier for this bot instance
            config: Bot-specific configuration
            system_config: System-wide configuration
            ib_connection_manager: Shared IB connection manager
        """
        super().__init__(bot_id, config, system_config, ib_connection_manager)
    
    async def start(self) -> None:
        """Start the bot."""
        # Log at different levels
        self.logger.info("Bot starting")
        self.logger.debug("Debug information")
        
        # Get shared IB connection
        host = self.system_config.get("connection.host")
        port = self.system_config.get("connection.port")
        client_id = self.system_config.get("connection.client_id")
        self.ib = await self.ib_connection_manager.connect(host, port, client_id)
        
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
        
        # Don't disconnect - the shared connection is managed by the framework
        self.ib = None
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

