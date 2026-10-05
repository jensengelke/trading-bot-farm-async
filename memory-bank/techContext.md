# Technical Context

## Technology Stack

### Core Technologies

- **Python 3.12**: Primary programming language
- **asyncio**: Built-in async framework for concurrent operations and task scheduling
- **ib_async (>=2.1.0)**: Asynchronous client library for Interactive Brokers API, including `IBC` and `Watchdog` wrappers
- **SQLite 3**: Embedded transactional database for virtual position tracking (via standard library `sqlite3`)
- **PyYAML (>=6.0)**: YAML parsing for configuration files
- **Pydantic**: Data validation and settings management using type annotations
- **croniter**: Cron expression parsing
- **pytz / tzdata**: Timezone management for scheduled trading windows (`America/New_York`)

### Platform & Development Environment

- **Primary Platform**: Windows 11
- **Shell**: PowerShell / cmd.exe
- **IDE**: Visual Studio Code
- **IB API Location**: `c:\twsapi-latest\source\pythonclient`

## Development Setup

### Virtual Environment

```bash
# Create virtual environment
python -m venv venv

# Activate (Windows PowerShell)
.\venv\Scripts\Activate.ps1

# Activate (Windows Command Prompt)
venv\Scripts\activate.bat

# Install dependencies
pip install -r requirements.txt
```

### Dependencies (requirements.txt)

```
PyYAML>=6.0                              # Configuration file parsing
c:\twsapi-latest\source\pythonclient     # Interactive Brokers TWS API
ib_async>=2.1.0                          # Async wrapper for IB API (incl. IBC/Watchdog)
pydantic                                 # Configuration validation
croniter                                 # Cron expression parsing
pytz                                     # Timezone support
tzdata                                   # Timezone database
```

## Project Structure

```
trading-bot-farm-async/
├── trading_bot_farm.py          # Main entry point & CLI
├── show_db.py                   # SQLite schema and row inspection utility
├── requirements.txt             # Python dependencies
├── README.md                    # Project documentation
├── LICENSE                      # License file
├── .gitignore                   # Git ignore rules
├── .clinerules                  # Instructions for AI coding agent
│
├── framework/                   # Core framework infrastructure
│   ├── __init__.py
│   ├── bot_base.py              # Abstract base class for all bots
│   ├── bot_manager.py           # Bot discovery & lifecycle orchestrator
│   ├── ib_connection.py         # Shared IB connection manager, IBC & Watchdog
│   ├── request_tracker.py       # RequestTracker & ErrorDispatcher for targeted error logs
│   ├── position_manager.py      # SQLite persistence for VirtualPosition
│   ├── option_utils.py          # Delta strike search (find_option_by_delta)
│   ├── entry_conditions.py      # Market condition evaluators (SMA, Move, VIX)
│   ├── exit_conditions.py       # Exit condition evaluators (PositionDelta)
│   ├── logging_config.py        # Three-tier logging infrastructure
│   ├── decorators.py            # Utility decorators (@trace_all_methods)
│   └── config/
│       ├── __init__.py
│       └── system_config.py     # Two-tier YAML loader with Pydantic models
│
├── bots/                        # Bot implementations
│   ├── __init__.py
│   ├── strategy/                # Configurable multi-leg option strategy bot
│   │   ├── __init__.py
│   │   ├── bot.py               # Options spread execution engine
│   │   └── config.py            # Pydantic schema for strategy bots
│   └── verify/                  # Connection and historical data verification bot
│       ├── __init__.py
│       ├── bot.py               # Verifies stocks, options, and futures data
│       └── config.py            # Pydantic schema for verify bot
│
├── config/                      # Configuration instances
│   ├── demo/                    # Demo / paper trading environment
│   │   ├── config.yaml          # Public config (port 7497, client_id 2, demo_positions.db)
│   │   ├── .secret-config.yaml  # Secret credentials (gitignored)
│   │   └── *.yaml               # Bot configs (e.g. strategy_fkk.yaml, verify_*.yaml)
│   ├── live/                    # Production live trading environment
│   │   ├── config.yaml          # Public config (port 4001, client_id 1, live_positions.db)
│   │   ├── .secret-config.yaml  # Production secrets (gitignored)
│   │   └── *.yaml               # Live strategy configs
│   └── test-live/               # Testing against live account with small size
│       ├── config.yaml          # Public config (port 7496, client_id 1, test_live_positions.db)
│       └── *.yaml
│
├── data/                        # SQLite databases (gitignored)
│   ├── demo_positions.db        # Demo virtual position persistence
│   ├── live_positions.db        # Production virtual position persistence
│   └── test_live_positions.db   # Test-live virtual position persistence
│
├── logs/                        # Log files (gitignored)
│   ├── demo/                    # Logs for demo instance
│   ├── live/                    # Logs for live instance
│   └── test-live/               # Logs for test-live instance
│
├── docs/                        # Architecture & strategy documentation
│   ├── bot_implementation_guide.md
│   ├── butterfly.md             # Flexible butterfly strategy documentation
│   ├── entry_conditions.md       # Entry condition evaluator documentation
│   ├── request_tracking.md       # Targeted error routing documentation
│   ├── shared_connection.md      # Singleton connection pooling guide
│   └── todo_virtual_positions.md # Analysis of broker netting vs virtual positions
│
└── memory-bank/                 # Cline Memory Bank
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

- **Connection**: Requires IB Gateway or TWS running locally or remotely (port 4001/7496 for live, 7497 for demo).
- **Client ID**: Single shared connection per instance eliminates client ID contention.
- **IBC & Watchdog**: Requires valid TWS/Gateway and IBC paths when enabled; handles daily authentication and maintenance restarts.
- **API Wrapper**: Built on `ib_async (>=2.1.0)`.

### Persistence & Concurrency

- **Database**: SQLite 3 with dedicated database file per instance (`data/{instance}_positions.db`).
- **Event Loop**: Single thread running Python `asyncio` event loop. All network I/O and sleep timers must be asynchronous.
- **Thread Safety**: `RequestTracker` and `ErrorDispatcher` utilize threading locks for thread-safe operations across asynchronous event loop callbacks.

### Configuration Validation (Pydantic)

- **System Models** (`framework/config/system_config.py`):
  - `ConnectionConfig`: `host`, `port` (0-65535), `client_id` (>0), `selected_account`.
  - `FlexConfig`: `flex_token`, `flex_query_id`.
  - `DatabaseConfig`: `path` (SQLite database location).
  - `IbcConfig`: `enabled`, `twsVersion`, `gateway`, `tradingMode` ('live'/'paper'), `userid`, `password`, `twsPath`, `ibcPath`, `ibcIni`, `trading_days`, `maintain_connection_from`, `maintain_connection_until`, `maintain_connection_timezone`.
  - `ConfigModel`: Validates all sections with `extra = "forbid"`.
- **Bot Models**:
  - `StrategyBotConfig` (`bots/strategy/config.py`): Validates legs (`StrategyLegConfig`), strike selection methods, delta bounds, entry conditions, exit conditions, and scheduling.
  - `VerifyBotConfig` (`bots/verify/config.py`): Validates asset symbols and security types ('stock', 'option', 'future').

## Tool Usage Patterns

### Running the Framework

```bash
# Production live trading instance
python trading_bot_farm.py --config config/live

# Demo / paper trading simulation instance
python trading_bot_farm.py --config config/demo

# Test-live instance (testing with minimal real size)
python trading_bot_farm.py --config config/test-live
```

### Inspecting Virtual Positions

```bash
# View table schema and contents of the live database
python show_db.py data/live_positions.db

# View demo database
python show_db.py data/demo_positions.db
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

- **Shared Connection Pooling**: Implemented via `IBConnectionManager` singleton; all bots share one connection.
- **Rate Limiting**: IB API pacing rules are respected via async sleep pacing and batched strike queries.
- **Reconnection & Gateway Supervision**: IBC Watchdog monitors connection state and restarts the gateway during maintenance windows.
- **Targeted Error Dispatching**: Isolates errors by request ID to prevent log bloat.

### Database Performance

- **SQLite 3**: Standard library implementation with local file storage. Fast reads/writes for virtual positions without external database daemon overhead.

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
