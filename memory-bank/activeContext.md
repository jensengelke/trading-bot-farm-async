# Active Context

## Current Work Focus

The Trading Bot Farm framework is in an operational state with core functionality implemented and tested. The memory bank has been initialized to provide comprehensive documentation for future development work.

## Recent Changes

- Memory bank initialization completed with all core documentation files
- Framework structure documented including architecture, patterns, and technical details
- Project is ready for ongoing development and bot implementation

## Next Steps

1. **Bot Development**: Create additional bot types as needed for specific trading strategies
2. **Configuration Enhancement**: Add new configuration fields as requirements evolve
3. **Testing**: Implement automated testing for framework components
4. **Documentation**: Keep memory bank updated as new features are added

## Active Decisions and Considerations

### Framework Design
- **Async-first architecture**: All bot operations use asyncio for efficient concurrent execution
- **Convention-based discovery**: Bots are discovered automatically based on file structure
- **Instance isolation**: Multiple trading environments can run independently on the same machine

### Configuration Management
- **Two-tier config**: Public config (version controlled) and secret config (gitignored)
- **Pydantic validation**: Type-safe configuration with validation at startup
- **Dot notation access**: Easy nested configuration access via `config.get("section.field")`

### Logging Strategy
- **Three-tier logging**: Standard (INFO+), Error (WARNING+), Trace (DEBUG+)
- **Instance-specific logs**: Each configuration instance has its own log directory
- **Automatic rotation**: Previous logs backed up on each startup

## Important Patterns and Preferences

### Bot Implementation Pattern
```python
@trace_all_methods
class Bot(BotBase):
    async def start(self):
        # 1. Log startup
        # 2. Get configuration
        # 3. Connect to IB
        # 4. Execute bot logic
        # 5. Log completion
    
    async def stop(self):
        # 1. Log shutdown
        # 2. Cleanup resources
        # 3. Log completion
```

### Error Handling Pattern
- Always log errors with `exc_info=True` for full stack traces
- Let framework handle exceptions (don't swallow them)
- Use appropriate log levels (INFO for success, ERROR for failures)

### Configuration Access Pattern
- System config: `self.system_config.get("connection.host")`
- Bot config: `self.config.get("symbols", [])`
- Always provide sensible defaults for optional fields

## Learnings and Project Insights

### What Works Well
1. **Dynamic bot loading**: Makes it easy to add new bot types without framework changes
2. **Instance-based configuration**: Enables running multiple environments simultaneously
3. **Three-tier logging**: Provides right level of detail for different use cases
4. **Pydantic validation**: Catches configuration errors early, before trading begins

### Areas for Future Enhancement
1. **Shared IB connection**: Consider connection pooling for multiple bots
2. **Automated testing**: Add unit and integration tests for framework components
3. **Bot scheduling**: Implement cron-based scheduling for periodic bot execution
4. **Health monitoring**: Add health check endpoints for operational monitoring

### Key Constraints
- **IB API rate limits**: Bots must respect Interactive Brokers API rate limits
- **Single event loop**: All bots share one asyncio event loop per process
- **Windows platform**: Primary development and deployment on Windows 11
- **Client ID uniqueness**: Each IB connection requires unique client ID

## Current State

### Implemented Components
- ✅ Core framework (bot_base, bot_manager, logging_config, decorators)
- ✅ Configuration system (SystemConfig with Pydantic validation)
- ✅ Logging infrastructure (three-tier, instance-specific, auto-rotation)
- ✅ Example bot (verify bot for testing IB connection)
- ✅ Documentation (framework.md, bot_implementation_guide.md)
- ✅ Memory bank (complete initialization)

### Configuration Instances
- `config/live/`: Live trading configuration
- `config/paper/`: Paper trading configuration

### Bot Types
- `verify`: Test bot that verifies IB connection and retrieves historical data

## Development Environment

- **Python**: 3.12
- **Virtual Environment**: venv
- **IDE**: Visual Studio Code
- **Platform**: Windows 11
- **IB API**: Located at `c:\twsapi-latest\source\pythonclient`

## Notes for Future Sessions

- Memory bank files are located in `.clinerules/` directory
- Always read memory bank files at start of new sessions
- Update `activeContext.md` when making significant changes
- Update `progress.md` to track completed and pending work
- Keep `systemPatterns.md` updated when adding new architectural patterns
