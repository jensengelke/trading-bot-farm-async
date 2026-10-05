# Progress

## What Works

### Core Framework & Lifecycle ✅
- **Bot Manager**: Discovers, instantiates, and manages bot lifecycle via async tasks.
- **Bot Base**: Standardized lifecycle contract (`start()`, `stop()`, `cleanup()`) with dependency-injected connection manager, logger, and configuration.
- **Dynamic Loading**: Dynamically imports bot classes based on YAML `type` parameter using `importlib.util`.
- **Async Execution**: Concurrent execution of all bots in a single event loop with error isolation.
- **Graceful Shutdown**: Signal handling (SIGINT/SIGTERM) cancels chase orders, preserves bracket orders, and disconnects shared connections cleanly.

### Singleton IB Connection & Gateway Supervision ✅
- **Shared Connection Pooling (`IBConnectionManager`)**: Thread-safe singleton (`asyncio.Lock`) sharing a single IB socket and client ID across all bots, preventing client ID collision errors.
- **IBC & Watchdog Integration**: Automated headless IB Gateway/TWS lifecycle management, including automated logins and restarts.
- **Maintenance Windows**: Timezone-aware connection scheduler (`America/New_York`) that starts/stops Watchdog and disconnects during off-hours (`maintain_connection_from` to `maintain_connection_until` on specified `trading_days`).

### Targeted Error Dispatching ✅
- **Request Tracking (`RequestTracker`)**: Thread-safe mapping of IB API request IDs (`reqId`) to originating bot instances.
- **Targeted Error Dispatcher (`ErrorDispatcher`)**: Routes IB `errorEvent` callbacks exclusively to the bot that initiated the request, eliminating log pollution and duplicate warnings across bots sharing a socket.
- **System Fallback**: Untracked requests and global connection errors route cleanly to the system logger.

### Virtual Position Persistence ✅
- **SQLite State Management (`PositionManager`, `VirtualPosition`)**: Dedicated instance database (`data/{instance}_positions.db`) tracking position ID, bot ID, status, underlying, expiration, leg details, fill prices/times, bracket order IDs, and initial Greeks.
- **Netting Decoupling**: Solves the broker netting dilemma where overlapping strikes in multi-leg spreads cancel out in IB's net portfolio, preserving independent strategy identity and exit rules.
- **Database Inspection CLI (`show_db.py`)**: Utility for displaying database table schemas and row contents.

### Option Strategy Engine (`bots/strategy/`) ✅
- **Configurable Multi-Leg Execution**: Supports complex structures (Butterflies, Iron Condors, Bull Put Spreads, asymmetric spreads).
- **Target DTE & Expiration Selection**: Matches target DTE exactly or searches for closest expiration.
- **Trading Class Disambiguation**: Resolves `SPXW` weekly vs `SPX` standard monthly index options.
- **Delta-Based Strike Search (`framework/option_utils.py`)**: `find_option_by_delta` queries IB Greeks in batches with fallback search in alternate (ITM) directions.
- **Execution Algorithm**: Samples mid-prices over `mid_price_monitoring_period`, submits limit combo orders (`BAG`), and iteratively adjusts prices by `min_tick` towards fill (`max_price_adjustments`) within `min_premium` and `max_premium` limits.
- **Bracket Risk Management**: Places GTC Stop Loss and Take Profit combo orders with `outsideRth=True`.

### Condition Evaluators ✅
- **Entry Conditions (`EntryConditionEvaluator`)**: Short-circuits trade entry if market filters fail (`SMA`, `underlying_intraday_move`, `VIX`).
- **Exit Conditions (`ExitConditionEvaluator`)**: Evaluates open position Greeks via `calculate_position_greeks` and triggers closure on `position_delta` drift.

### Connectivity Verification Bot (`bots/verify/`) ✅
- Multi-asset connectivity testing for stocks, options, and futures.
- Dynamic contract resolution and 1-minute historical bar verification.

### Configuration System & Environments ✅
- **Environment Isolation**: Configured for `live`, `demo`, and `test-live` environments.
- **Two-Tier Merging**: Merges public `config.yaml` with sensitive `.secret-config.yaml`.
- **Pydantic Validation**: Strict schemas (`ConfigModel`, `ConnectionConfig`, `FlexConfig`, `DatabaseConfig`, `IbcConfig`, `StrategyBotConfig`, `VerifyBotConfig`).

### Logging Infrastructure ✅
- Three-tier logs (`*.log`, `*-error.log`, `*-trace.log`) for system and each bot.
- Automatic startup log rotation into timestamped backup folders (`backup-YYYYMMDD_HHMMSS/`).
- Method-level tracing decorator (`@trace_all_methods`).

## What's Left to Build

### 1. State Reconciliation Engine 🔲
- **Broker Net Reconciliation**: Periodically compare SQLite virtual positions against IB net portfolio positions (`reqPositions`).
- **Manual Intervention Detection**: If a position was closed or modified manually in TWS, mark the virtual position as closed and automatically cancel orphaned Take Profit / Stop Loss bracket orders.
- **Position Discrepancy Alerts**: Log warning when local virtual legs differ from broker net balances.

### 2. Automated Testing Suite 🔲
- **Unit Tests**: Test `PositionManager`, `RequestTracker`, `ErrorDispatcher`, `EntryConditionEvaluator`, and Pydantic models.
- **Mock IB Connection**: Mock `ib_async` objects (`IB`, `Contract`, `Ticker`, `Trade`) to run automated tests without IB Gateway.
- **Integration Tests**: Verify end-to-end bot lifecycle, scheduling, price chase adjustments, and shutdown sequences.

### 3. Operational Alerts & Notifications 🔲
- **Real-Time Webhook/Alerts**: Push notifications (Telegram, Discord, Slack, or Email) on order fills, price adjustments, bracket triggers, and critical errors.
- **Daily Performance Reports**: Summary reports comparing opening premium vs exit fills using Flex queries.

### 4. Advanced Trading Features 🔲
- **Multi-Entry Butterflies**: Scale into positions over multiple entry windows.
- **Dynamic Adjustments**: Rolling legs or delta-hedging positions when exit condition thresholds are approached.
- **Web Dashboard**: Read-only browser UI to visualize open virtual positions and log streams.

## Current Status

### Phase 1: Foundation (Complete) ✅
- [x] Core framework implementation & dynamic bot loading
- [x] Two-tier configuration system with Pydantic validation
- [x] Three-tier logging infrastructure with startup backup rotation
- [x] Verification bot (`verify`) for multi-asset testing

### Phase 2: Option Strategy Engine & Connection Pooling (Complete) ✅
- [x] Multi-leg option spread strategy engine (`bots/strategy/`)
- [x] Singleton `IBConnectionManager` with asyncio thread-safe pooling
- [x] Delta-based strike selection (`find_option_by_delta`) with ITM alternate search
- [x] Mid-price monitoring and iterative tick chase algorithm
- [x] GTC bracket order submission (`StopOrder` & `LimitOrder` outside RTH)
- [x] Scheduled cycle execution loops (`entry_days`, `entry_times`, pytz)

### Phase 3: Production Hardening & Persistence (Complete) ✅
- [x] Headless IBC and `Watchdog` automation with scheduled maintenance windows
- [x] Targeted error dispatching (`RequestTracker` and `ErrorDispatcher`)
- [x] SQLite virtual position tracking (`PositionManager`, `VirtualPosition`)
- [x] Database inspection CLI tool (`show_db.py`)
- [x] Condition evaluators (Entry: `SMA`, `underlying_intraday_move`, `VIX`; Exit: `position_delta`)
- [x] Clean shutdown logic preserving bracket orders while cancelling in-flight orders

### Phase 4: State Reconciliation & Test Suite (Active) 🔲
- [ ] Active portfolio reconciliation loop against broker positions
- [ ] Automated unit and mock testing suite
- [ ] Real-time notification webhooks

## Known Issues & Technical Debt

### Known Limitations
- **Manual Intervention Sync**: Closing or modifying legs manually inside TWS leaves orphaned bracket orders in IB; automated cancellation via reconciliation loop is not yet active.
- **Illiquid Strike Mid-Prices**: In fast-moving or wide-spread market conditions, mid-price sampling may require longer monitoring periods or wider premium bounds.

### Technical Debt
- **Automated Test Coverage**: Core components have been validated via live and demo runs, but lack a formal unit test suite with mocked IB network responses.
- **Order Tracking in SQLite**: While opening order fills and bracket order IDs are stored, bracket order execution fills are not yet automatically updated to `status = 'CLOSED'` via execution callbacks.

## Milestones

### Milestone 1: MVP Foundation (Completed) ✅
- Core framework operational, dynamic loading, multi-tier logging.

### Milestone 2: Strategy Engine & Connection Pooling (Completed) ✅
- Shared IB connection singleton, multi-leg combo execution, delta strike selection, mid-price chasing.

### Milestone 3: Production Hardening (Completed) ✅
- IBC/Watchdog scheduled maintenance, targeted error routing, SQLite virtual position persistence, entry/exit condition engines.

### Milestone 4: Portfolio Reconciliation & Testing (Next)
- Real-time reconciliation loop for manual TWS trades, automated test suite, external webhook alerting.
