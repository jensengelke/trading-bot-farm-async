# System Patterns

## Architecture Overview

The Trading Bot Farm uses an asynchronous, layered architecture with centralized connection pooling, targeted error routing, and database-backed virtual position persistence:

```
┌────────────────────────────────────────────────────────────────────────┐
│                   trading_bot_farm.py (Entry Point)                    │
│             - CLI arguments, signal handlers (SIGINT/SIGTERM)          │
│             - Three-tier logging initialization (System & Bots)        │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│                       BotManager (Orchestrator)                        │
│             - Dynamic bot discovery & class loading                    │
│             - Asyncio Task management & lifecycle coordination         │
│             - Holds shared IBConnectionManager & SystemConfig          │
└─────────┬─────────────────────────┬──────────────────────────┬─────────┘
          │                         │                          │
┌─────────▼────────┐      ┌─────────▼────────┐       ┌─────────▼────────┐
│   SystemConfig   │      │IBConnectionMgr   │       │ RequestTracker & │
│ - Two-tier merge │      │- Singleton pool  │       │ ErrorDispatcher  │
│ - Pydantic models│      │- IBC / Watchdog  │       │- reqId -> bot_id │
│ - Dot access     │      │- Maint. windows  │       │- Targeted errors │
└──────────────────┘      └─────────┬────────┘       └─────────┬────────┘
                                    │                          │
          ┌─────────────────────────┴──────────────────────────┘
          │ (Shared IB instance & targeted dispatch)
          ▼
┌────────────────────────────────────────────────────────────────────────┐
│                         BotBase Implementations                        │
│                                                                        │
│  ┌─────────────────────────────────┐  ┌─────────────────────────────┐  │
│  │   bots/strategy/bot.py          │  │   bots/verify/bot.py        │  │
│  │   - Scheduled execution loop    │  │   - Multi-asset bar check   │  │
│  │   - Entry/exit evaluators       │  │   - Stocks, options, futures│  │
│  │   - Delta strike search         │  │   - Connectivity test       │  │
│  │   - Mid-price sampling & chase  │  └─────────────────────────────┘  │
│  │   - GTC bracket order placement │                                   │
│  │   - PositionManager (SQLite)    │                                   │
│  └────────────────┬────────────────┘                                   │
└───────────────────┼────────────────────────────────────────────────────┘
                    │
                    ▼
┌────────────────────────────────────────┐
│   SQLite Database (data/*.db)          │
│   - Table: virtual_positions           │
│   - Decoupled from broker net position │
│   - Inspected via show_db.py           │
└────────────────────────────────────────┘
```

## Key Components

### 1. Entry Point (trading_bot_farm.py)

**Responsibilities:**
- Parse command-line arguments (`--config <path>`).
- Initialize the three-tier logging subsystem for the instance directory.
- Instantiate `BotManager` with the specified configuration directory.
- Install Windows-compatible signal handlers (`SIGINT`, `SIGTERM`) using an `asyncio.Event`.
- Execute `bot_manager.run()` and ensure complete, graceful cleanup of all bots and connections upon termination.

**Key Patterns:**
- `asyncio.run(main())` hosts the single async event loop.
- `asyncio.wait([run_task, shutdown_task], return_when=FIRST_COMPLETED)` handles interrupt signals cleanly.
- `finally:` block executes `await bot_manager.stop_all_bots()` and shuts down logging handlers.

### 2. BotManager (framework/bot_manager.py)

**Responsibilities:**
- Discover bot configuration YAMLs in the active config directory (excluding `config.yaml` and `.secret-config.yaml`).
- Dynamically import bot classes (`bots/{type}/bot.py`) via `importlib.util`.
- Instantiate bots, injecting `bot_id`, `config`, `system_config`, and the singleton `ib_connection_manager`.
- Manage lifecycle: create async tasks for `bot.start()`, handle `bot.stop()`, cancel tasks, and call `bot.cleanup()`.
- Shut down the shared IB connection on framework termination.

**Bot Instantiation Signature:**
```python
bot_class = self.load_bot_class(bot_type)
bot_instance = bot_class(bot_id, config, self.system_config, self.ib_connection_manager)
```

### 3. IBConnectionManager & Gateway Lifecycle (framework/ib_connection.py)

**Responsibilities:**
- Provide a thread-safe singleton connection (`get_ib_connection_manager()`) using `asyncio.Lock`.
- Ensure multiple bots reuse the same active `IB()` instance without client ID collisions.
- Manage automated IB Gateway / TWS process lifecycle using IBC and `Watchdog`.
- Implement `_connection_maintenance_loop()`: checks `_is_in_maintenance_window()` against configured `trading_days`, `maintain_connection_from`, and `maintain_connection_until` in `maintain_connection_timezone` (`America/New_York`), starting/stopping Watchdog and disconnecting during off-hours.
- Register global `errorEvent` handler and route events to `ErrorDispatcher`.

### 4. Targeted Error Dispatching (framework/request_tracker.py)

**Responsibilities:**
- Solve log pollution from shared IB connections where all errors broadcast to all listeners.
- **`RequestTracker` (Singleton)**: Maps `reqId -> bot_id`. Bots register request IDs when initiating API requests (`track_request(req_id)`).
- **`ErrorDispatcher` (Singleton)**: Intercepts IB `errorEvent(reqId, errorCode, errorString, contract)`. Looks up `bot_id` via `RequestTracker` and invokes the bot's specific error handler. Untracked or system-level errors fall back to the system logger.

### 5. Virtual Position Persistence (framework/position_manager.py)

**Responsibilities:**
- Maintain an SQLite-backed state store (`data/{instance}_positions.db`) for all virtual strategy positions.
- Decouple strategy combo identities from IBKR's net portfolio netting (which cancels overlapping legs).
- **`VirtualPosition` Model**:
  - `position_id`: UUID string primary key.
  - `bot_id`, `status` ("PENDING", "OPEN", "CLOSED").
  - `underlying_symbol`, `expiration`, `quantity`.
  - `legs_data`: JSON-serialized dictionary with leg parameters (symbol, strike, right, ratio, conId).
  - `opening_order_ref`, `opening_fill_price`, `opening_fill_time`.
  - `tp_order_id`, `sl_order_id`, `exit_conditions`, `initial_greeks`.
  - `closing_fill_price`, `closing_fill_time`, `created_at`, `updated_at`.
- **`PositionManager` Methods**:
  - `create_position(position)`
  - `update_position(position)`
  - `get_position(position_id)`
  - `get_open_positions(bot_id=None)`
  - `get_position_by_order_ref(order_ref)`

### 6. Option Utilities & Strike Resolution (framework/option_utils.py)

**Responsibilities:**
- Provide `find_option_by_delta()` to locate option strikes matching a target delta using IB market data Greeks.
- Processes strikes in batches (default: 20) via `ib.qualifyContractsAsync()` and `ib.reqMktData(contract, "106")`.
- If the best strike match lies on the boundary (suggesting the search moved in the wrong direction), automatically searches `alternate_strikes` (e.g. ITM strikes).

### 7. Entry & Exit Condition Engines (framework/entry_conditions.py, framework/exit_conditions.py)

**Responsibilities:**
- **`EntryConditionEvaluator`**: Evaluates strategy pre-conditions; short-circuits entry if any condition fails:
  - `SMACondition`: Compares current underlying price against an N-day SMA calculated from historical daily bars (`ib.reqHistoricalDataAsync`).
  - `UnderlyingIntradayMoveCondition`: Compares current underlying price against today's open price.
  - `VIXCondition`: Checks current VIX level against a threshold.
- **`ExitConditionEvaluator`**: Evaluates whether open positions should be closed early:
  - `PositionDeltaCondition`: Compares calculated position delta (via `calculate_position_greeks`) against threshold limits.

### 8. SystemConfig (framework/config/system_config.py)

**Responsibilities:**
- Load and deep merge `config.yaml` and `.secret-config.yaml`.
- Strict Pydantic validation (`extra = "forbid"`):
  - `ConnectionConfig`: `host`, `port`, `client_id`, `selected_account`.
  - `FlexConfig`: `flex_token`, `flex_query_id`.
  - `DatabaseConfig`: `path` (relative or absolute path to SQLite file).
  - `IbcConfig`: `enabled`, `twsVersion`, `gateway`, `tradingMode`, `userid`, `password`, `twsPath`, `ibcPath`, `ibcIni`, `trading_days`, `maintain_connection_from`, `maintain_connection_until`, `maintain_connection_timezone`.
- Dot-notation getter (`config.get("database.path")`).

### 9. BotBase (framework/bot_base.py)

**Responsibilities:**
- Abstract base class with constructor:
  `def __init__(self, bot_id: str, config: Dict[str, Any], system_config: Any, ib_connection_manager: 'IBConnectionManager')`
- Injects logger (`get_bot_logger(bot_id)`).
- Registers with `ErrorDispatcher`: `_error_dispatcher.register_bot_handler(self.bot_id, self._on_ib_error)`.
- Helper methods for request tracking: `track_request(req_id)` and `untrack_request(req_id)`.
- Enforces abstract `start()` and `stop()`.
- Implements `cleanup()` to clear tracked requests and unregister error handlers.

### 10. Concrete Bot Implementations

- **Strategy Bot (`bots/strategy/bot.py`)**:
  - Implements scheduled option spread trading (Butterflies, Iron Condors, Bull Put Spreads).
  - Handles strike selection (`underlying_offset`, `leg_offset`, `delta`).
  - Disambiguates SPX vs SPXW trading class.
  - Monitors mid-prices for `mid_price_monitoring_period` seconds.
  - Places limit combo orders (`BAG`) and chases execution with iterative tick adjustments up to `max_price_adjustments`.
  - Places GTC bracket orders (Stop Loss, Take Profit) marked `outsideRth=True`.
  - Persists virtual positions in SQLite via `PositionManager`.
  - Preserves bracket orders on shutdown while cancelling unfilled opening orders.
- **Verify Bot (`bots/verify/bot.py`)**:
  - Validates market data connectivity for stocks, options, and futures.
  - Resolves contract types dynamically and logs 1-minute historical bars.

### 11. Logging & Method Tracing (framework/logging_config.py, framework/decorators.py)

**Responsibilities:**
- Three-tier log files per bot and system: Standard (`INFO+`), Error (`WARNING+`), Trace (`DEBUG+`).
- Startup rotation to `backup-YYYYMMDD_HHMMSS/`.
- Class decorator `@trace_all_methods` automatically tracing sync/async method execution.

## Critical Implementation Paths

### Bot Startup Sequence

```
1. User runs: python trading_bot_farm.py --config config/live
2. Initialize 3-tier logging for 'live' instance directory.
3. BotManager loads SystemConfig (merges config.yaml & .secret-config.yaml, validates Pydantic ConfigModel).
4. BotManager gets singleton IBConnectionManager.
5. BotManager discovers bot configs (*.yaml excluding system configs).
6. For each bot config:
   a. Extract 'type' field and dynamically load bots/{type}/bot.py.
   b. Instantiate BotClass(bot_id, config, system_config, ib_connection_manager).
   c. BotBase registers error handler with ErrorDispatcher.
   d. Bot validates bot-specific Pydantic config (e.g., StrategyBotConfig).
   e. Create asyncio task for bot.start().
7. Bot.start():
   a. Requests shared IB connection: ib = await ib_connection_manager.connect(host, port, client_id, ibc_config).
   b. If IBC enabled, Watchdog starts Gateway if needed and maintains connection during trading hours.
   c. Bot enters scheduled execution loop (America/New_York timezone).
8. Event loop runs until SIGINT/SIGTERM.
```

### Strategy Bot Trading Cycle

```
1. Calculate next scheduled execution time from entry_days & entry_times in configured timezone.
2. Sleep until execution time (checking every 10s for stop requests).
3. Evaluate Entry Conditions (SMA, underlying_intraday_move, VIX):
   - If any condition fails, abort cycle and wait for next schedule.
4. Resolve Target Expiration (exact DTE or nearest available).
5. Strike Selection for each leg:
   - 'underlying_offset': price + offset
   - 'leg_offset': parent_leg_strike + offset
   - 'delta': find_option_by_delta() batch search comparing IB greeks
6. Qualify contracts (resolving SPXW for SPX weekly options).
7. Mid-Price Sampling:
   - Subscribe to market data for all legs for mid_price_monitoring_period seconds.
   - Calculate mean combo mid-price.
8. Order Placement & Chasing:
   - Create BAG combo contract with ComboLeg entries.
   - Place LimitOrder at mean mid-price.
   - Loop up to max_price_adjustments: wait price_adjustment_wait_seconds; if unfilled, adjust limit price by minTick towards fill (respecting min_premium / max_premium).
9. Fill & Risk Management:
   - If filled, record VirtualPosition in SQLite via PositionManager.
   - Place bracket orders if configured: Stop Loss (StopOrder) and Take Profit (LimitOrder) with outsideRth=True.
10. Exit Condition Monitoring:
    - Periodically check PositionDeltaCondition; close position if delta exceeds threshold.
```

### Bot Shutdown Sequence

```
1. User presses Ctrl+C (SIGINT) or sends SIGTERM.
2. Signal handler sets shutdown_event.
3. BotManager.stop_all_bots() initiated:
   - For each running bot:
     a. Call bot.stop().
     b. In StrategyBot: preserve bracket orders (_stoploss, _takeprofit) while cancelling unfilled chase orders.
     c. Clear bot.ib reference (do not disconnect shared socket).
     d. Cancel and await bot's asyncio Task.
     e. Call bot.cleanup() -> unregisters ErrorDispatcher handler and clears RequestTracker.
4. BotManager disconnects shared IB connection: await ib_connection_manager.disconnect().
   - Stops Watchdog and closes shared IB socket.
5. Close log handlers and exit cleanly.
```

### Configuration Access Pattern

```python
# In bot implementation:
host = self.system_config.get("connection.host")
port = self.system_config.get("connection.port")
client_id = self.system_config.get("connection.client_id")
db_path = self.system_config.get("database.path")
ibc_config = self.system_config.get("ibc")

# System config: shared across all bots
# Bot config: validated Pydantic model (self.validated_config) specific to this bot instance
```

## Design Decisions

### Why Async?

- **Concurrent Execution**: Multiple bots run simultaneously in a single thread without locking bottlenecks.
- **IB Integration**: `ib_async` library is async-native, providing non-blocking API requests.
- **Resource Efficiency**: Handles multiple scheduled timers and market data subscriptions with near-zero idle CPU load.

### Why Dynamic Loading?

- **Extensibility**: Add new bot types (e.g., futures, equities, spreads) without altering framework orchestrators.
- **Isolation**: Each bot package encapsulates its execution logic and Pydantic validation schema.

### Why Instance-based Config (demo, live, test-live)?

- **Multi-Environment**: Run demo, test-live, and live trading side-by-side with distinct ports, accounts, and SQLite files.
- **Safety**: Hard separation prevents test configurations from placing live orders.

### Why Shared IB Connection?

- **Zero Collisions**: IB Gateway allows only one socket per client ID; sharing one connection avoids socket disconnects and reconnections.
- **Socket Efficiency**: Subscriptions and market data feeds share one pipe.

### Why Request Tracking & Error Dispatching?

- **Targeted Logs**: Eliminates log spam where one bot's API error is echoed in every running bot's log file.
- **Root Cause Isolation**: Immediate attribution of errors to the exact bot that created the `reqId`.

### Why Virtual Positions in SQLite?

- **Decoupled from Broker Netting**: When multiple strategies trade the same underlying and expiry with overlapping strikes, IB consolidates them into net portfolio quantities. SQLite preserves individual strategy trade identity, entry prices, initial Greeks, and bracket order IDs.

## Common Patterns in Bot Implementation

### Modern Bot Structure

```python
from framework.bot_base import BotBase
from framework.decorators import trace_all_methods
from bots.strategy.config import StrategyBotConfig

@trace_all_methods
class Bot(BotBase):
    def __init__(self, bot_id: str, config: dict, system_config: any, ib_connection_manager):
        super().__init__(bot_id, config, system_config, ib_connection_manager)
        
        # 1. Validate bot configuration via Pydantic
        self.validated_config = StrategyBotConfig(**config)
        self.ib = None
        self._stop_requested = False
    
    async def start(self):
        self.logger.info("Starting bot")
        
        # 2. Get connection settings
        host = self.system_config.get("connection.host")
        port = self.system_config.get("connection.port")
        client_id = self.system_config.get("connection.client_id")
        ibc_config = self.system_config.get("ibc")
        
        # 3. Connect via shared connection manager
        self.ib = await self.ib_connection_manager.connect(host, port, client_id, ibc_config)
        
        # 4. Run scheduled cycle loop
        while not self._stop_requested:
            await self._run_scheduled_cycle()
        
        self.logger.info("Bot completed")
    
    async def stop(self):
        self.logger.info("Stopping bot")
        self._stop_requested = True
        
        # 5. Clean up non-bracket orders while preserving GTC StopLoss/TakeProfit
        if self._active_trades and self.ib:
            for trade in self._active_trades:
                if trade.orderStatus.status not in ["Filled", "Cancelled"]:
                    order_ref = trade.order.orderRef or ""
                    if "_stoploss" in order_ref or "_takeprofit" in order_ref:
                        self.logger.info(f"Preserving bracket order: {order_ref}")
                    else:
                        self.ib.cancelOrder(trade.order)
        
        # Release reference; framework manages connection lifecycle
        self.ib = None
        self.logger.info("Bot stopped")
```

### Error Handling Pattern

```python
def _on_ib_error(self, reqId: int, errorCode: int, errorString: str, contract) -> None:
    # Called automatically by ErrorDispatcher only for requests originated by this bot
    if contract:
        self.logger.warning(f"IB Error [reqId={reqId}, code={errorCode}]: {errorString} | Contract: {contract}")
    else:
        self.logger.warning(f"IB Error [reqId={reqId}, code={errorCode}]: {errorString}")
```

## Extension Points

### Adding a New Bot Type
1. Create directory: `bots/{new_type}/`
2. Create config schema: `bots/{new_type}/config.py` (Pydantic model)
3. Create implementation: `bots/{new_type}/bot.py` extending `BotBase`
4. Implement `start()` and `stop()` accepting `(bot_id, config, system_config, ib_connection_manager)`
5. Create bot YAML: `config/{instance}/{bot_id}.yaml` with `type: new_type`

### Adding Condition Evaluators
1. Implement subclass of `EntryCondition` or `ExitCondition` in `framework/entry_conditions.py` or `framework/exit_conditions.py`
2. Register subclass in `CONDITION_TYPES`
3. Add Pydantic schema in `bots/strategy/config.py`

### Inspecting Database
- Run `python show_db.py data/live_positions.db` to view all tables, schema, and persisted virtual positions.
