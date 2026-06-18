# Product Context

## Why This Project Exists

The Trading Bot Farm framework was created to solve the challenge of running multiple trading strategies simultaneously while maintaining clean separation of concerns, shared infrastructure, and operational efficiency.

### Problems It Solves

1. **Multi-Strategy Trading**: Enables traders to run multiple independent trading strategies (bots) concurrently without needing separate processes or infrastructure for each
2. **Configuration Management**: Provides a structured approach to managing both system-wide and bot-specific configuration, with proper separation of sensitive data
3. **Environment Isolation**: Supports multiple trading environments (paper trading, live trading, multiple accounts) through instance-based configuration
4. **Observability**: Comprehensive logging infrastructure makes it easy to monitor, debug, and audit bot behavior
5. **Resource Efficiency**: Bots share a single connection to Interactive Brokers and common runtime resources

## How It Should Work

### User Workflow

1. **Setup**: User creates a configuration instance directory (e.g., `config/live/`)
2. **System Configuration**: User configures connection settings in `config.yaml` and sensitive data in `.secret-config.yaml`
3. **Bot Configuration**: User creates YAML files for each bot instance they want to run (e.g., `verify_stocks.yaml`)
4. **Execution**: User starts the framework with `python trading_bot_farm.py --config config/live`
5. **Operation**: Framework discovers all bot configurations, instantiates them, and runs them concurrently
6. **Monitoring**: User monitors bot behavior through instance-specific log files
7. **Shutdown**: User stops the framework (Ctrl+C), which gracefully shuts down all bots

### Key Behaviors

- **Automatic Discovery**: Framework automatically finds and loads all bot configurations in the specified instance directory
- **Independent Operation**: Each bot runs independently with its own configuration and logging
- **Shared Resources**: Bots share system configuration (IB connection settings, credentials)
- **Graceful Lifecycle**: Framework manages clean startup and shutdown of all bots
- **Error Isolation**: If one bot fails, others continue running (framework handles exceptions)

## User Experience Goals

### For Bot Developers

- **Simple Interface**: Implement just two methods (`start()` and `stop()`) to create a new bot
- **Rich Context**: Bots receive their configuration, system configuration, and a pre-configured logger
- **Easy Debugging**: Automatic method tracing and comprehensive logging make debugging straightforward
- **Type Safety**: Pydantic validation catches configuration errors early

### For Bot Operators

- **Easy Configuration**: YAML-based configuration is human-readable and easy to modify
- **Clear Logging**: Three-tier logging (standard, error, trace) provides appropriate detail for different needs
- **Multiple Environments**: Run paper trading and live trading simultaneously on the same machine
- **Audit Trail**: Log rotation preserves historical logs for analysis and compliance

### For System Administrators

- **Security**: Sensitive configuration is kept separate and never committed to version control
- **Reliability**: Graceful shutdown ensures clean resource cleanup
- **Monitoring**: Instance-specific logs make it easy to monitor multiple deployments
- **Extensibility**: New bot types can be added without modifying the framework

## Design Philosophy

The framework follows these core principles:

1. **Convention over Configuration**: Sensible defaults and clear naming conventions reduce configuration burden
2. **Fail Fast**: Configuration validation happens at startup, not during trading
3. **Explicit is Better**: Clear separation between system config, bot config, and bot implementation
4. **Async by Default**: Built on asyncio for efficient concurrent operations
5. **Developer Friendly**: Rich logging, type hints, and clear abstractions make development pleasant
