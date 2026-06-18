# Technical Context

## Technology Stack

### Core Technologies

- **Python 3.12**: Primary programming language
- **asyncio**: Built-in async framework for concurrent operations
- **ib_async (>=2.1.0)**: Asynchronous client library for Interactive Brokers API
- **PyYAML (>=6.0)**: YAML parsing for configuration files
- **Pydantic**: Data validation and settings management using Python type annotations
- **croniter**: Cron expression parsing (for scheduled bot operations)
- **pytz / tzdata**: Timezone handling

### Platform

- **Primary Platform**: Windows 11
- **Shell**: cmd.exe (Windows Command Prompt)
- **IDE**: Visual Studio Code
- **IB API Location**: `c:\twsapi-latest\source\pythonclient`

## Development Setup

### Virtual Environment

```bash
# Create virtual environment
python -m venv venv

# Activate (Windows)
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### Dependencies

```
PyYAML>=6.0                              # Configuration file parsing
c:\twsapi-latest\source\pythonclient     # Interactive Brokers TWS API
ib_async>=2.1.0                          # Async wrapper for IB API
pydantic                                 # Configuration validation
croniter                                 # Cron expression parsing
pytz                                     # Timezone support
tzdata                                   # Timezone database
```

## Project Structure

```
trading-bot-farm-async/
├── trading_bot_farm.py          # Main entry point
├── requirements.txt             # Python dependencies
├── README.md                    # Project documentation
├── LICENSE                      # License file
├── .gitignore                   # Git ignore rules
│
├── framework/                   # Core framework code
│   ├── __init__.py
│   ├── bot_base.py             # Abstract base class for bots
│   ├── bot_manager.py          # Bot lifecycle management
│   ├── logging_config.py       # Logging infrastructure
│   ├── decorators.py           # Utility decorators (@trace_all_methods)
│   └── config/
│       └── system_config.py    # Configuration management
│
├── bots/                        # Bot implementations
│   ├── __init__.py
│   └── verify/                 # Example: verify bot
│       ├── __init__.py
│       └── bot.py              # Bot implementation
│
├── config/                      # Configuration instances
│   ├── live/                   # Live trading instance
│   │   ├── config.yaml         # Public configuration
│   │   ├── .secret-config.yaml # Sensitive configuration (gitignored)
│   │   └── *.yaml              # Bot instance configs
│   └── paper/                  # Paper trading instance
│       ├── config.yaml
│       ├── .secret-config.yaml
│       └── *.yaml
│
├── logs/                        # Log files (gitignored)
│   ├── live/                   # Logs for live instance
│   │   ├── system.log
│   │   ├── system-error.log
│   │   ├── system-trace.log
│   │   ├── {bot_id}.log
│   │   ├── {bot_id}-error.log
│   │   ├── {bot_id}-trace.log
│   │   └── backup-{timestamp}/ # Rotated logs
│   └── paper/                  # Logs for paper instance
│
├── docs/                        # Documentation
│   ├── framework.md
│   └── bot_implementation_guide.md
│
└── .clinerules/                 # Cline AI memory bank
    ├── memory-bank.md
    ├── projectbrief.md
    ├── productContext.md
    ├── systemPatterns.md
    ├── techContext.md
    ├── activeContext.md
    └── progress.md
```

## Technical Constraints

### Interactive Brokers Integration

- **Connection**: Requires IB Gateway or TWS running locally or remotely
- **Client ID**: Each connection requires a unique client ID
- **Port**: 
  - Paper trading: typically 7497
  - Live trading: typically 7496
- **API Version**: Uses TWS API 10.19+ (via ib_async wrapper)

### Asynchronous Architecture

- **Event Loop**: Single asyncio event loop per process
- **Concurrency**: Multiple bots run as concurrent tasks
- **IB Connection**: Shared connection pool (one per bot or shared instance)
- **Non-blocking**: All I/O operations must be async

### Configuration Validation

- **Pydantic Models**: Enforce type safety and validation rules
- **Required Fields**: 
  - `connection.host` (string)
  - `connection.port` (integer, 0-65535)
  - `connection.client_id` (positive integer)
  - `connection.selected_account` (string)
  - `flex.flex_token` (string)
  - `flex.flex_query_id` (string)
- **Validation Timing**: At startup, before any bots are instantiated

### Logging Infrastructure

- **Log Levels**: DEBUG, INFO, WARNING, ERROR, CRITICAL
- **Three-Tier System**:
  - Standard logs: INFO and above
  - Error logs: WARNING and above
  - Trace logs: All levels (DEBUG+)
- **Encoding**: UTF-8 for all log files
- **Rotation**: Automatic on startup, previous logs moved to timestamped backup

## Tool Usage Patterns

### Running the Framework

```bash
# Start with specific configuration instance
python trading_bot_farm.py --config config/live

# Or with paper trading
python trading_bot_farm.py --config config/paper
```

### Development Workflow

1. **Create Bot Type**: Add new directory under `bots/`
2. **Implement Bot**: Create `bot.py` with `Bot` class extending `BotBase`
3. **Configure Bot**: Add YAML file to config instance directory
4. **Test**: Run framework and monitor logs
5. **Debug**: Check trace logs for detailed execution flow

### Git Workflow

- **Tracked**: Framework code, bot implementations, public config, documentation
- **Ignored**: Logs, secret config files, virtual environment, `__pycache__`

### Testing

- **Manual Testing**: Run framework with test configurations
- **Log Analysis**: Review trace logs for debugging
- **IB Connection**: Verify connection to IB Gateway/TWS before running

## Key Technical Decisions

### Why ib_async?

- **Async-native**: Built on asyncio, matches framework architecture
- **Pythonic API**: Clean, intuitive interface for IB operations
- **Active Maintenance**: Well-maintained library with good documentation
- **Type Hints**: Supports modern Python type checking

### Why Pydantic?

- **Type Safety**: Catches configuration errors at startup
- **Validation**: Built-in validators for common patterns
- **Documentation**: Models serve as living documentation
- **IDE Support**: Excellent autocomplete and type checking

### Why YAML?

- **Human-readable**: Easy to read and edit
- **Comments**: Supports inline documentation
- **Hierarchical**: Natural structure for nested configuration
- **Standard**: Well-supported across tools and languages

### Why Instance-based Configuration?

- **Isolation**: Separate environments don't interfere
- **Flexibility**: Easy to add new instances
- **Security**: Sensitive data isolated per instance
- **Parallel Execution**: Run multiple instances simultaneously

## Performance Considerations

### Asyncio Event Loop

- **Single Thread**: All bots run in single thread via asyncio
- **Non-blocking**: I/O operations don't block other bots
- **Task Switching**: Efficient context switching between bot tasks
- **Scalability**: Can handle dozens of bots efficiently

### IB Connection

- **Connection Pooling**: Consider shared connection for multiple bots
- **Rate Limiting**: IB API has rate limits, bots must respect them
- **Reconnection**: Handle connection drops gracefully
- **Client ID Management**: Ensure unique client IDs per connection

### Logging Performance

- **Buffered I/O**: Python logging uses buffered writes
- **Async-safe**: Logging is thread-safe and async-safe
- **Rotation**: Log rotation happens at startup, not during operation
- **Trace Logs**: DEBUG logging has minimal performance impact

## Security Considerations

### Sensitive Configuration

- **Separation**: `.secret-config.yaml` files are gitignored
- **Merge Strategy**: Secret config overrides public config
- **Access Control**: File system permissions protect sensitive data
- **No Hardcoding**: Never hardcode credentials in code

### API Keys and Tokens

- **Flex Token**: Stored in `.secret-config.yaml`
- **Account Numbers**: Stored in `.secret-config.yaml`
- **Connection Details**: Can be in public config (localhost) or secret config (remote)

### Logging Security

- **No Secrets in Logs**: Avoid logging sensitive data
- **Log Access**: Restrict access to log directories
- **Audit Trail**: Logs provide audit trail for compliance
