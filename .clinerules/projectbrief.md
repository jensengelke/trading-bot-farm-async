# Project Brief

## Project Name
Trading Bot Farm (Async)

## Project Purpose
A Python-based asynchronous trading bot framework that enables multiple trading bots to run concurrently, sharing common infrastructure, configuration, and runtime resources while connecting to Interactive Brokers (IB) for trading operations.

## Core Requirements

### 1. Multi-Bot Architecture
- Support multiple independent bot instances running in parallel
- Each bot has its own configuration file and unique instance ID
- Bots share system-wide configuration (connection settings, credentials)
- Dynamic bot discovery and loading from configuration directory

### 2. Configuration System
- Instance-based configuration (e.g., `config/live`, `config/paper`)
- System configuration split between:
  - `config.yaml` - Technical configuration (version controlled)
  - `.secret-config.yaml` - Sensitive configuration (gitignored)
- Bot-specific configuration files (e.g., `verify_stocks.yaml`)
- Configuration validation using Pydantic

### 3. Bot Lifecycle Management
- Framework manages bot discovery, instantiation, start, and stop
- Each bot implements standard lifecycle methods: `start()` and `stop()`
- Graceful shutdown handling with signal handlers
- Asynchronous execution using Python asyncio

### 4. Logging Infrastructure
- Instance-specific log directories (e.g., `logs/live/`)
- Three log levels per component:
  - Standard logs (INFO and above)
  - Error logs (WARNING and above)
  - Trace logs (all levels including DEBUG)
- Automatic log rotation on startup (previous logs moved to timestamped backup)
- Separate logs for system and each bot instance
- Method tracing decorator for debugging

### 5. Interactive Brokers Integration
- Connection to IB Gateway/TWS via ib_async library
- Configurable connection parameters (host, port, client_id, account)
- Support for market data retrieval and trading operations

## Technology Stack
- **Language**: Python 3.12
- **Async Framework**: asyncio
- **IB Integration**: ib_async (>=2.1.0)
- **Configuration**: PyYAML, Pydantic
- **Platform**: Windows (with cross-platform considerations)

## Project Structure
```
trading-bot-farm-async/
├── trading_bot_farm.py          # Main entry point
├── framework/                    # Core framework code
│   ├── bot_base.py              # Abstract base class for bots
│   ├── bot_manager.py           # Bot lifecycle management
│   ├── logging_config.py        # Logging setup
│   ├── decorators.py            # Utility decorators
│   └── config/
│       └── system_config.py     # Configuration management
├── bots/                        # Bot implementations
│   └── {bot_type}/
│       └── bot.py               # Bot implementation
├── config/                      # Configuration instances
│   └── {instance_name}/
│       ├── config.yaml          # Public config
│       ├── .secret-config.yaml  # Private config
│       └── {bot_id}.yaml        # Bot configs
└── logs/                        # Log files
    └── {instance_name}/
        ├── system*.log          # System logs
        └── {bot_id}*.log        # Bot logs
```

## Key Design Principles
1. **Separation of Concerns**: Framework, bots, and configuration are clearly separated
2. **Extensibility**: New bot types can be added without modifying framework
3. **Configuration Flexibility**: Support multiple trading environments (paper, live, etc.)
4. **Observability**: Comprehensive logging at system and bot levels
5. **Async-First**: Built on asyncio for efficient concurrent operations
6. **Type Safety**: Pydantic validation for configuration
7. **Security**: Sensitive configuration kept separate and gitignored

## Success Criteria
- Multiple bots can run concurrently in a single process
- Each bot operates independently with its own configuration
- System provides robust logging and error handling
- Configuration is validated and type-safe
- Framework is extensible for new bot implementations
- Graceful startup and shutdown of all components
