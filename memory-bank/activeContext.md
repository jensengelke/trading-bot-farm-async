# Active Context

## Current Work Focus

The Trading Bot Farm is an operational asynchronous options trading farm executing multi-leg options spreads on Interactive Brokers across multiple environments (`live`, `demo`, `test-live`). The current focus is operational monitoring, strategy execution stability, and preparing the state reconciliation engine to synchronize SQLite virtual positions with broker net portfolios.

## Recent Changes & Architectural Evolution

- **Singleton IB Connection Pooling**: Replaced per-bot IB connections with `IBConnectionManager` (`framework/ib_connection.py`). Uses an `asyncio.Lock` to ensure all bots share a single TCP socket and client ID, eliminating client ID conflicts and socket overhead.
- **IBC & Watchdog Automation**: Integrated headless IB Gateway lifecycle management. `IBConnectionManager` runs `_connection_maintenance_loop()` to supervise Watchdog during market hours (`America/New_York` timezone) across configured `trading_days`, restarting the Gateway automatically across nightly maintenance reboots.
- **Targeted Error Dispatching**: Implemented `RequestTracker` and `ErrorDispatcher` (`framework/request_tracker.py`). When bots submit API calls, their `reqId` is registered. IB `errorEvent` callbacks look up the originating bot, eliminating duplicate error logs and routing messages exclusively to the relevant bot logger.
- **Virtual Position Persistence**: Built `PositionManager` and `VirtualPosition` (`framework/position_manager.py`) with SQLite storage (`data/{instance}_positions.db`). Decouples strategy combo spreads from IBKR's consolidated net portfolio positions, ensuring multiple overlapping spreads (e.g., butterflies sharing center strikes) retain independent identities, fill records, Greeks, and bracket order IDs.
- **Option Strategy Engine (`bots/strategy/`)**:
  - Implemented multi-leg option spreads (Butterflies, Iron Condors, Bull Put Spreads) on SPX and equities.
  - Added delta-based strike selection (`framework/option_utils.py` / `find_option_by_delta`) with batched market data requests and alternate strike direction (ITM/OTM) searching.
  - Implemented SPX vs `SPXW` trading class resolution to prevent ambiguous contract errors on index options.
  - Implemented mid-price sampling over `mid_price_monitoring_period` and iterative price adjustments stepping by `min_tick` towards fill.
  - Fixed price adjustment calculations for negative limit prices (credit spreads vs debit spreads).
  - Attached GTC bracket orders (Stop Loss & Take Profit) with `outsideRth=True`.
  - Refined shutdown handling: preserves active bracket orders while cancelling in-flight limit chase orders.
- **Condition Evaluators**: Implemented pre-trade market filters in `framework/entry_conditions.py` (`SMA`, `underlying_intraday_move`, `VIX`) and post-entry exit checks in `framework/exit_conditions.py` (`position_delta`).
- **Database Inspection Tool**: Created `show_db.py` CLI script to inspect table schemas and active virtual positions.
- **Environment Structure**: Standardized environments to `config/live`, `config/demo`, and `config/test-live` (all references to `paper` eliminated). Pydantic models updated to include `DatabaseConfig` and `IbcConfig`.

## Next Steps

1. **Portfolio State Reconciliation Loop**:
   - Implement periodic comparison between SQLite virtual positions and actual broker portfolio (`ib.reqPositions()`).
   - If legs are closed manually in TWS, mark virtual positions as closed and cancel orphaned bracket orders (`tp_order_id`, `sl_order_id`).
2. **Bracket Execution Fills**:
   - Listen to `execDetails` / `orderStatus` callbacks to update virtual position status to `CLOSED` and record closing fill price and timestamp.
3. **Automated Unit & Integration Testing**:
   - Build a test suite using `pytest` and `unittest.mock` to simulate IB API callbacks without needing a live Gateway.
4. **Alerts & Notifications**:
   - Add webhook dispatchers for Discord/Telegram alerts on order entry, fills, bracket execution, and error events.

## Active Decisions and Considerations

### Virtual Position Decoupling
- **Context**: When trading multiple multi-leg options strategies on the same underlying/expiry, strikes frequently overlap. IBKR consolidates portfolio holdings into single net contract amounts, erasing individual strategy context.
- **Decision**: All positions are tracked internally as `VirtualPosition` objects inside SQLite (`PositionManager`). The database is the source of truth for strategy state, entry fills, and profit targets.

### Shared Connection vs Per-Bot Sockets
- **Context**: IB Gateway limits connections per client ID, and running multiple sockets causes client ID collisions and socket overhead.
- **Decision**: `IBConnectionManager` provides a singleton connection shared across all bots. Request attribution and error isolation are handled by `RequestTracker` and `ErrorDispatcher`.

### Bracket Order Preservation on Shutdown
- **Context**: When stopping bots, pending limit orders being adjusted should be cancelled, but protective Stop Loss and Take Profit orders already on the book must not be dropped.
- **Decision**: `Bot.stop()` inspects `orderRef`: any order containing `_stoploss` or `_takeprofit` is preserved, while in-flight opening orders are cancelled.

### Headless Maintenance Windows
- **Context**: IB Gateway disconnects nightly and requires weekend restarts.
- **Decision**: IBC and Watchdog are integrated with schedule-aware connection windows configured in `America/New_York` timezone, ensuring automated reconnection without human intervention.

## Important Patterns and Conventions

### Strategy Bot Constructor & Lifecycle
```python
@trace_all_methods
class Bot(BotBase):
    def __init__(self, bot_id: str, config: dict, system_config: any, ib_connection_manager):
        super().__init__(bot_id, config, system_config, ib_connection_manager)
        self.validated_config = StrategyBotConfig(**config)
        self.position_manager = PositionManager(self.system_config.get("database.path"), self.logger)
        self.ib = None

    async def start(self):
        self.ib = await self.ib_connection_manager.connect(...)
        # Main execution loop
```

### Error Dispatching Pattern
- Never subscribe directly to `ib.errorEvent` inside bot implementations.
- Override `_on_ib_error(self, reqId, errorCode, errorString, contract)` in `BotBase` subclasses; `ErrorDispatcher` invokes this only for requests originating from this bot instance.

### Configuration Model Hierarchy
- `SystemConfig` merges `config.yaml` and `.secret-config.yaml` into `ConfigModel` (`ConnectionConfig`, `FlexConfig`, `DatabaseConfig`, `IbcConfig`).
- Bot instances validate their own specific Pydantic models (`StrategyBotConfig`, `VerifyBotConfig`).

## Current State

### Active Configuration Environments
- `config/live/`: Production live trading with real capital (`data/live_positions.db`).
- `config/demo/`: Paper trading / demo simulation (`data/demo_positions.db`).
- `config/test-live/`: Live testing with minimal position sizing (`data/test_live_positions.db`).

### Active Bot Types
- `strategy`: Flexible multi-leg options spread bot (Butterflies, Iron Condors, Bull Put Spreads).
- `verify`: Connectivity verification bot across stocks, options, and futures.

### Persistence Files
- `data/live_positions.db`: SQLite database for live trading virtual positions.
- `data/demo_positions.db`: SQLite database for demo trading virtual positions.
- `data/test_live_positions.db`: SQLite database for test-live trading virtual positions.

## Notes for Future Sessions
- Memory Bank files are located in `memory-bank/` directory.
- Always read all files in `memory-bank/` at the start of a task.
- Update `activeContext.md` and `progress.md` as work progresses.
