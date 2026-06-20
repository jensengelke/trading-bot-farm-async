# Shared IB Connection

## Overview

The Trading Bot Farm framework now uses a **shared IB connection** that is reused across all bot instances. This eliminates the "client id already in use" error and improves resource efficiency.

## Architecture

### IBConnectionManager

The `framework/ib_connection.py` module provides the `IBConnectionManager` class, which:

- **Singleton Pattern**: A single global instance manages the IB connection
- **Thread-Safe**: Uses asyncio locks to ensure safe concurrent access
- **Connection Reuse**: First bot creates the connection, subsequent bots reuse it
- **Centralized Disconnect**: Connection is closed when the framework shuts down

### Key Components

```
┌─────────────────────────────────────────┐
│         BotManager                      │
│  - Creates IBConnectionManager          │
│  - Passes to all bots                   │
│  - Disconnects on shutdown              │
└─────────────────┬───────────────────────┘
                  │
        ┌─────────┴─────────┐
        │                   │
┌───────▼────────┐  ┌──────▼──────────┐
│ Bot Instance 1 │  │ Bot Instance 2  │
│ - Calls        │  │ - Calls         │
│   connect()    │  │   connect()     │
│ - Gets shared  │  │ - Gets shared   │
│   IB instance  │  │   IB instance   │
└────────────────┘  └─────────────────┘
        │                   │
        └─────────┬─────────┘
                  │
        ┌─────────▼─────────────────────┐
        │  IBConnectionManager          │
        │  - Single IB() instance       │
        │  - Shared across all bots     │
        └───────────────────────────────┘
```

## How It Works

### Bot Startup

1. **First Bot**: Calls `ib_connection_manager.connect(host, port, client_id)`
   - Creates new `IB()` instance
   - Connects to IB Gateway/TWS
   - Returns the connection

2. **Subsequent Bots**: Call `ib_connection_manager.connect(host, port, client_id)`
   - Detects existing connection
   - Returns the same `IB()` instance
   - Logs "Reusing existing IB connection"

### Bot Shutdown

- **Individual Bots**: Set `self.ib = None` (don't disconnect)
- **Framework Shutdown**: `BotManager.stop_all_bots()` calls `ib_connection_manager.disconnect()`

## Implementation Details

### In BotManager

```python
from framework.ib_connection import get_ib_connection_manager

class BotManager:
    def __init__(self, config_dir: str):
        # ...
        self.ib_connection_manager = get_ib_connection_manager()
    
    def instantiate_bot(self, bot_id: str) -> BotBase:
        # Pass connection manager to each bot
        bot_instance = bot_class(bot_id, config, self.system_config, self.ib_connection_manager)
        return bot_instance
    
    async def stop_all_bots(self) -> None:
        # Stop all bots first
        for bot_id in bot_ids:
            await self.stop_bot(bot_id)
        
        # Then disconnect shared connection
        await self.ib_connection_manager.disconnect()
```

### In BotBase

```python
class BotBase(ABC):
    def __init__(self, bot_id: str, config: Dict[str, Any], system_config: Any, ib_connection_manager: 'IBConnectionManager'):
        self.bot_id = bot_id
        self.config = config
        self.system_config = system_config
        self.ib_connection_manager = ib_connection_manager  # New parameter
        self.logger = get_bot_logger(bot_id)
```

### In Bot Implementation

```python
class Bot(BotBase):
    async def start(self) -> None:
        # Get connection parameters
        host = self.system_config.get("connection.host")
        port = self.system_config.get("connection.port")
        client_id = self.system_config.get("connection.client_id")
        
        # Get shared connection (creates or reuses)
        self.ib = await self.ib_connection_manager.connect(host, port, client_id)
        
        # Use the connection
        bars = await self.ib.reqHistoricalDataAsync(...)
    
    async def stop(self) -> None:
        # Don't disconnect - just clear reference
        self.ib = None
```

## Benefits

1. **No Client ID Conflicts**: Only one connection uses the client ID
2. **Resource Efficiency**: Single connection instead of N connections for N bots
3. **Faster Startup**: Subsequent bots don't need to establish new connections
4. **Simplified Management**: Framework handles connection lifecycle

## Migration Guide

### For Existing Bots

If you have existing bot implementations, update them as follows:

#### 1. Update `__init__` signature

**Before:**
```python
def __init__(self, bot_id: str, config: Dict[str, Any], system_config: Any):
    super().__init__(bot_id, config, system_config)
```

**After:**
```python
def __init__(self, bot_id: str, config: Dict[str, Any], system_config: Any, ib_connection_manager):
    super().__init__(bot_id, config, system_config, ib_connection_manager)
```

#### 2. Update connection logic in `start()`

**Before:**
```python
self.ib = IB()
await self.ib.connectAsync(host, port, clientId=client_id)
```

**After:**
```python
self.ib = await self.ib_connection_manager.connect(host, port, client_id)
```

#### 3. Update `stop()` method

**Before:**
```python
if self.ib and self.ib.isConnected():
    self.ib.disconnect()
```

**After:**
```python
# Don't disconnect - the shared connection is managed by the framework
self.ib = None
```

## Configuration

No configuration changes are required. The framework uses the same `connection` settings from `config.yaml`:

```yaml
connection:
  host: "127.0.0.1"
  port: 7496
  client_id: 1  # Single client ID for all bots
```

## Logging

The shared connection logs its activity:

```
[system] Creating new IB connection to 127.0.0.1:7496 with client_id=1
[system] Successfully connected to IB at 127.0.0.1:7496
[verify_futures] Getting shared IB connection
[verify_futures] Using shared IB connection
[verify_options] Getting shared IB connection
[system] Reusing existing IB connection to 127.0.0.1:7496 with client_id=1
[verify_options] Using shared IB connection
[verify_stocks] Getting shared IB connection
[system] Reusing existing IB connection to 127.0.0.1:7496 with client_id=1
[verify_stocks] Using shared IB connection
```

## Thread Safety

The `IBConnectionManager` uses an asyncio lock to ensure thread-safe access:

```python
async def connect(self, host: str, port: int, client_id: int) -> IB:
    async with self._connection_lock:
        # Only one bot can create/access connection at a time
        if self._connected and self._ib and self._ib.isConnected():
            return self._ib  # Reuse existing
        # Create new connection
        self._ib = IB()
        await self._ib.connectAsync(host, port, clientId=client_id)
        return self._ib
```

## Future Enhancements

Potential improvements to the shared connection system:

1. **Connection Health Monitoring**: Automatic reconnection on disconnect
2. **Connection Pooling**: Multiple connections with different client IDs
3. **Per-Bot Client IDs**: Automatic client ID assignment from a pool
4. **Connection Metrics**: Track connection usage and performance
