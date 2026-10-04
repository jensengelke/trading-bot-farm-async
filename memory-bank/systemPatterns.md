# System Patterns

## Architecture Overview

The Trading Bot Farm uses a layered architecture with clear separation between framework, bots, and configuration:

```
┌─────────────────────────────────────────┐
│     trading_bot_farm.py (Entry Point)   │
└─────────────────┬───────────────────────┘
                  │
┌─────────────────▼───────────────────────┐
│         BotManager (Orchestrator)       │
│  - Bot discovery & instantiation        │
│  - Lifecycle management                 │
│  - Task coordination                    │
└─────────────────┬───────────────────────┘
                  │
        ┌─────────┴─────────┐
        │                   │
┌───────▼────────┐  ┌──────▼──────────┐
│  SystemConfig  │  │   Bot Instances │
│  - YAML merge  │  │   (BotBase)     │
│  - Validation  │  │   - start()     │
│  - Dot access  │  │   - stop()      │
└────────────────┘  └─────────────────┘
```

## Key Components

### 1. Entry Point (trading_bot_farm.py)

**Responsibilities:**
- Parse command-line arguments
- Initialize logging system
- Create and run BotManager
- Handle shutdown signals (SIGINT, SIGTERM)
- Ensure graceful cleanup

**Key Patterns:**
- Uses `asyncio.run()` as the main event loop entry
- Signal handlers set an event flag for graceful shutdown
- Finally block ensures cleanup even on errors

### 2. BotManager (framework/bot_manager.py)

**Responsibilities:**
- Discover bot configurations in the config directory
- Load and validate bot configurations
- Dynamically import bot implementations
- Instantiate and manage bot lifecycle
- Coordinate concurrent bot execution

**Key Patterns:**
- **Dynamic Module Loading**: Uses `importlib.util` to load bot modules at runtime
- **Convention-based Discovery**: Finds bots by scanning for YAML files (excluding system configs)
- **Task Management**: Creates asyncio tasks for each bot's `start()` method
- **Error Isolation**: Catches exceptions per-bot to prevent cascade failures

**Bot Discovery Flow:**
```python
1. Scan config directory for *.yaml files
2. Exclude config.yaml and .secret-config.yaml
3. For each bot config:
   - Extract 'type' field
   - Load bots/{type}/bot.py
   - Instantiate Bot class
   - Call start() in separate task
```

### 3. SystemConfig (framework/config/system_config.py)

**Responsibilities:**
- Load and merge config.yaml and .secret-config.yaml
- Validate configuration using Pydantic models
- Provide dot-notation access to config values

**Key Patterns:**
- **Deep Merge**: Secret config overrides public config at all nesting levels
- **Pydantic Validation**: Ensures type safety and required fields at startup
- **Dot Notation**: `config.get("connection.host")` for nested access
- **Fail Fast**: Validation errors prevent startup

**Configuration Models:**
```python
ConfigModel
├── ConnectionConfig (host, port, client_id, selected_account)
└── FlexConfig (flex_token, flex_query_id)
```

### 4. BotBase (framework/bot_base.py)

**Responsibilities:**
- Define bot interface (abstract base class)
- Provide common bot infrastructure (logger, config access)
- Enforce lifecycle contract

**Key Patterns:**
- **Abstract Base Class**: Forces implementation of `start()` and `stop()`
- **Dependency Injection**: Receives bot_id, config, and system_config in constructor
- **Pre-configured Logger**: Each bot gets its own logger instance

### 5. Logging System (framework/logging_config.py)

**Responsibilities:**
- Initialize instance-specific logging
- Create three-tier log files (standard, error, trace)
- Rotate logs on startup
- Provide logger instances for system and bots

**Key Patterns:**
- **Instance Isolation**: Logs organized by instance name (e.g., `logs/live/`)
- **Three-Tier Logging**: 
  - Standard: INFO+
  - Error: WARNING+
  - Trace: DEBUG+ (all levels)
- **Automatic Rotation**: Previous logs moved to timestamped backup directory
- **UTF-8 Encoding**: Ensures proper character handling

### 6. Decorators (framework/decorators.py)

**Responsibilities:**
- Provide method tracing for debugging
- Automatically log method entry/exit/exceptions

**Key Patterns:**
- **@trace_all_methods**: Class decorator that wraps all public methods
- **Async-aware**: Handles both sync and async methods
- **Debug Level**: Traces logged at DEBUG level (only in trace logs)

## Critical Implementation Paths

### Bot Startup Sequence

```
1. User runs: python trading_bot_farm.py --config config/live
2. Initialize logging for 'live' instance
3. Create BotManager with config directory
4. BotManager loads SystemConfig (merges YAML files)
5. BotManager.discover_bots() finds bot configs
6. For each bot:
   a. Load bot config YAML
   b. Extract 'type' field
   c. Import bots/{type}/bot.py
   d. Instantiate Bot(bot_id, config, system_config)
   e. Create asyncio task for bot.start()
7. Wait for all bot tasks or shutdown signal
```

### Bot Shutdown Sequence

```
1. User presses Ctrl+C or sends SIGTERM
2. Signal handler sets shutdown_event
3. Main loop detects shutdown_event
4. BotManager.stop_all_bots() called
5. For each bot:
   a. Call bot.stop()
   b. Cancel bot's asyncio task
   c. Wait for task cancellation
6. Shutdown logging system
7. Exit cleanly
```

### Configuration Access Pattern

```python
# In bot implementation:
host = self.system_config.get("connection.host")
port = self.system_config.get("connection.port")
symbols = self.config.get("symbols", [])

# System config: shared across all bots
# Bot config: specific to this bot instance
```

## Design Decisions

### Why Async?

- **Concurrent Execution**: Multiple bots run simultaneously without threads
- **IB Integration**: ib_async library is async-native
- **Efficient I/O**: Non-blocking network operations
- **Scalability**: Can handle many bots with minimal overhead

### Why Dynamic Loading?

- **Extensibility**: Add new bot types without modifying framework
- **Isolation**: Each bot type is self-contained
- **Convention**: Simple file structure (bots/{type}/bot.py)

### Why Instance-based Config?

- **Multi-Environment**: Run paper and live trading on same machine
- **Isolation**: Each instance has separate logs and config
- **Flexibility**: Easy to add new instances (e.g., multiple accounts)

### Why Pydantic Validation?

- **Type Safety**: Catch config errors at startup
- **Documentation**: Models serve as config documentation
- **Validation**: Enforce constraints (e.g., port range, positive client_id)

### Why Three-Tier Logging?

- **Development**: Trace logs with DEBUG for detailed debugging
- **Operations**: Standard logs with INFO for normal monitoring
- **Alerts**: Error logs with WARNING+ for issues requiring attention
- **Performance**: Avoid cluttering standard logs with debug info

## Common Patterns in Bot Implementation

### Typical Bot Structure

```python
from framework.bot_base import BotBase
from framework.decorators import trace_all_methods

@trace_all_methods
class Bot(BotBase):
    def __init__(self, bot_id, config, system_config):
        super().__init__(bot_id, config, system_config)
        # Initialize bot-specific state
        self.ib = None
    
    async def start(self):
        # 1. Log startup
        self.logger.info("Starting bot")
        
        # 2. Get configuration
        host = self.system_config.get("connection.host")
        symbols = self.config.get("symbols", [])
        
        # 3. Connect to IB
        self.ib = IB()
        await self.ib.connectAsync(host, port, clientId=client_id)
        
        # 4. Execute bot logic
        # ...
        
        # 5. Log completion
        self.logger.info("Bot completed")
    
    async def stop(self):
        # 1. Log shutdown
        self.logger.info("Stopping bot")
        
        # 2. Cleanup resources
        if self.ib and self.ib.isConnected():
            self.ib.disconnect()
        
        # 3. Log completion
        self.logger.info("Bot stopped")
```

### Error Handling Pattern

```python
try:
    result = await some_operation()
    self.logger.info(f"Operation succeeded: {result}")
except Exception as e:
    self.logger.error(f"Operation failed: {e}", exc_info=True)
    raise  # Let framework handle the error
```

## Extension Points

### Adding a New Bot Type

1. Create directory: `bots/{new_type}/`
2. Create file: `bots/{new_type}/bot.py`
3. Implement `Bot` class extending `BotBase`
4. Implement `start()` and `stop()` methods
5. Create bot config: `config/{instance}/{bot_id}.yaml` with `type: new_type`

### Adding Configuration Fields

1. Update Pydantic models in `framework/config/system_config.py`
2. Add fields to `config.yaml` or `.secret-config.yaml`
3. Access via `system_config.get("section.field")`

### Adding Custom Logging

```python
# In bot:
self.logger.debug("Detailed debug info")
self.logger.info("Normal operation")
self.logger.warning("Potential issue")
self.logger.error("Error occurred", exc_info=True)
```
