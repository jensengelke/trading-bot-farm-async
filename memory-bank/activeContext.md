# Active Context

## Telegram Notifier Refactoring & DAX Spread Observation Bot (2026-11-04)

- Split notification infrastructure into framework services and specialized bots:
  - **Framework Level**:
    - `framework/telegram_client.py`: Added `get_telegram_client` factory to dynamically resolve credentials from system config without bot-level boilerplate.
    - `framework/account_service.py`: Added safe, timeout-guarded snapshot queries (`fetch_account_summary`, `fetch_portfolio_positions`, `fetch_open_orders`, `fetch_account_snapshot`).
    - `framework/formatters.py`: Unified HTML formatters for connection transitions, order lifecycles, fills, and daily account status summaries.
    - `framework/option_spreads.py`: Robust credit spread pairing engine (`identify_spread_positions`) and protection auditor (`audit_take_profit_orders`) detecting Bear Call Spreads and Bull Put Spreads across single and rolling multiple-spread scenarios.
  - **`bots/telegram/` (Account & Order Lifecycle Watchdog)**:
    - Dedicated to connection events (`connectedEvent`, `disconnectedEvent`), order placement/status/cancellations, fills with commissions, and daily account health broadcasts.
  - **`bots/dax_spread_observation/` (Dedicated DAX Manual Spreads Risk Monitor)**:
    - Discovers manual DAX spreads from `ib.portfolio()`.
    - Alerts on short leg delta drift (`|delta| > max_short_delta`, default 0.38).
    - Alerts on short strike testing/breaching (`buffer <= strike_tested_buffer_points`).
    - Alerts on long strike testing/breaching (`long_buffer <= 0`).
    - Audits GTC Take-Profit orders from `ib.openTrades()`.
    - Delivers scheduled daily summaries with unrealized P&L, delta, and buffer metrics.
    - Integrated with alert cooldown to avoid notification spam.
  - Enhanced `audit_take_profit_orders` to recognize TWS position closing orders (where right-clicking an open spread and clicking "Close" creates a BAG combo with outer action `SELL` and negative limit price, e.g. `SELL 5x @ -3.00 LMT GTC`).
  - Extended DAX spread observation daily summary to list all active open orders on the DAX index with human-readable strike and expiration descriptions.
  - Resolved missing Greeks/delta on DAX options by ensuring the option contract exchange is qualified as `"EUREX"` prior to market data subscription, successfully streaming live delta computations.
  - Updated daily summary formatters to explicitly label `short leg Δ` and display actual delta values.


  - Added `tests/test_dax_spread_observation.py` with 10 passing tests (123 total unit tests passing).


## Bot Documentation Suite Created (2026-11-03)

- Created comprehensive documentation for each of the four framework bots stored in `docs/`:
  - `docs/verify.md`: Multi-asset IB connectivity and market data verification bot (`verify`). Details root and symbol-level parameters, asset class behaviors (stocks, futures, options with index/stock auto-detection, Friday expiration targeting, ATM call quotes), and complete configuration tables.
  - `docs/strategy.md`: Multi-leg option spread strategy execution engine (`strategy`). Documents root parameters, legs, entry conditions (SMA, intraday move, VIX), exit conditions, mid-price sampling and iterative tick chasing, signed premium conventions, and GTC bracket orders.
  - `docs/telegram.md`: Real-time asynchronous notification bot (`telegram`). Documents notification triggers (submission, status, fills with commissions, cancellations, price adjustments), account/orderRef filtering, credential isolation in `.secret-config.yaml`, and alert samples.
  - `docs/optionomega.md`: OptionOmega strategy execution bot (`optionomega`). Documents strict Pydantic models (`extra: 'forbid'`), underlying definitions, leg specs (delta, parent offset, underlying offset, fixed strike), allocation, frequency and floating entry windows with atomic daily latches, quote quality filtering, bounded limit chaser, signed canonical targets (`M - E`), broker GTC bracket exits with OCA groups, first-leg expiration closing policy, and schema v4 persistence.


## Verify Bot Option & Index Option Enhancement (2026-11-03)

- Fixed verify bot hanging during option qualification in `bots/verify/bot.py`:
  - Resolved `reqSecDefOptParamsAsync` hang by passing qualified non-zero `underlyingConId` (instead of hardcoded `0`, which caused IB warning 321 that never ended the response future) and `futFopExchange=""`.
  - Added timeout guards (`asyncio.wait_for`) around contract qualification and option chain retrieval.
  - Implemented intelligent underlying resolution (`_resolve_underlying`) supporting index underlyings (such as DAX on EUREX/DTB, SPX on CBOE) as well as stock underlyings (TSLA, etc.).
  - Implemented `_find_target_friday_expiration` to target this Friday's expiration (or nearest upcoming Friday / holiday fallback).
  - Implemented ATM strike selection using live underlying market price with fallback to middle strike.
  - Added option market data retrieval in `start()` to log and print live bid and ask prices for the ATM Friday call.
  - Updated `SymbolConfig` in `bots/verify/config.py` with `right` defaulting to `"C"` and `security_type` optional (`None` for auto-detect).
  - Added comprehensive test suite `tests/test_verify_bot.py` (9 tests, all passing; full suite 113 tests pass).


## OptionOmega Bot Implementation (2026-11-03)

- Implemented `docs/optionomega_bot_plan.md` Phase 1 core runtime:
  - **OptionOmega bot (`bots/optionomega/`)**:
    - `pricing.py`: Canonical P/L (`M - E`), target marks (`percent`, `fixed_premium`, `closing_premium`), tick rounding, premium filters (explicit credit/debit vs OO side-agnostic), limit constraint (`constrain_entry_limit`), post-execution slippage measurement.
    - `config.py`: Strict nested Pydantic models (`extra: 'forbid'`). Rejects `backtest_only` and any slippage configuration keys. Validates unique leg IDs, acyclic parent offset links, stop loss stop-limit offset, mutually exclusive premium filter families, floating window times, and first-expiry policy constraints.
    - `settings_report.py`: Mapping and analysis utility for OptionOmega backtest settings against live runtime disposition.
    - `bot.py`: Complete `BotBase` orchestrator connecting market data, scheduler, broker exits, limit chaser, and position monitor.
    - `docs/fixtures/optionomega_calendar_example.yaml`: Canonical documentation fixture.
  - **Clean Schema v4 (`framework/db_schema.py`)**:
    - `SCHEMA_VERSION = 4`. Older database files (v1-v3) are rejected on open with clear guidance to archive or delete them (no in-place migration).
    - Tables: `bots`, `entry_latches`, `virtual_positions` (`status` CHECK constraint across 8 allowed states), `position_legs` (single source of truth for legs and expiries), `orders` (typed columns `account`, `oca_group`, `oca_type`, `trigger_method`, `outside_rth`, `reference_price`), `fills`, `order_events` (audit trail).
  - **Database Export/Import Tool (`framework/db_transfer.py`)**:
    - Bumped to `FORMAT_VERSION = 2`, `SCHEMA_VERSION = 4`. Handles all 7 tables in FK dependency order. Rejects older export formats (< 2) or schemas (< 4).
  - **Framework Services (`framework/`)**:
    - `market_data_service.py`: Shared reference-counted ticker subscriptions per contract, freshness validation, spread mark and natural bid/ask calculations.
    - `entry_scheduler.py`: Exchange-aware trading days and floating entry window in `America/New_York` timezone; atomic daily consumption via `entry_latches`.
    - `broker_exits.py`: Broker GTC Take Profit (LMT) and Stop Loss (STP or STP LMT) with broker OCA group.
    - `limit_chaser.py`: Bounded entry chase within `max_adverse_price_change`, soft exit chase from mid toward natural side (never beyond natural side, never becomes a market order).
    - `position_monitor.py`: Earliest leg deadline monitoring (`close_full_position_before_first_leg_expires`), race-safe exit execution (locks position, cancels competing broker exits, awaits confirmation, runs soft exit chaser).
  - **PositionManager & OrderService**:
    - Adapted to schema v4. `create_position` takes legs list and persists to `position_legs`.
    - Guard against modifying terminal orders (`OrderStatus.DoneStates`).
    - Awaitable cancel confirmation (`wait_for_cancel`).
    - Protection rejection callback hook (`add_protection_rejected_listener`).
    - Overfill detection: a racing second fill on an already `CLOSED` position moves it to `NEEDS_RECONCILIATION`.
  - **Strategy Bot Adaption**:
    - `bots/strategy/bot.py` adapted to schema v4 (passes legs to `create_position`).
  - **Test Suite**:
    - 104 unit tests pass across all test modules.


## OptionOmega Bot Planning (2026-11-02)

- Consolidated agreed design in `docs/optionomega_bot_plan.md`; planning deliverable only, no new trading runtime enabled.
- Broker GTC limit PT; broker GTC stop or stop-limit SL, defaulting to stop only when SL is configured. BAG/OCA acceptance must be paper-validated; existing submission code does not prove broker support.
- Four explicit credit/debit opening bounds; mutually exclusive non-null OO compatibility bounds (user accepted validation error). OO filtering is positive, side-agnostic, after entry slippage; keep this separate from live chase budgets.
- No implicit entry/exit rules. Explicit first-leg-expiry close boolean; false is valid for calendars and requires residual-leg/assignment reconciliation, not whole-position closure at first expiry.
- Floating entry is Phase 1; consume and persist the trading-day latch when conditions fire, before selection/submission, even if no fill follows.
- Correction to older statements below: ExitConditionEvaluator is constructed but never evaluated (no exit monitoring loop). The earlier claim that the strategy bot does not call persistence is OUTDATED since commit 3fcc5f0 (see "Framework-Owned Order Persistence" above).
- Plan revised after commit 3fcc5f0 (2026-11-02): `docs/optionomega_bot_plan.md` now has a "Persistence baseline" section (6) and builds on `OrderService`/schema v3 instead of a parallel layer. Key decisions: new tables `entry_latches` (PK `bot_id, trading_date`, insert-only), `position_legs` (PK `position_id, leg_id`), `order_events` (UUID); `orders.order_json` for OCA/trigger/outsideRth/account; keep PENDING as the entry-pending state; everything that must survive a YAML edit keys on `bot_id`, not `bot_version_id` (the version hash covers the whole YAML, including `backtest_only`); every new table must be added to `db_transfer.TABLES` with a `FORMAT_VERSION` bump and migration tests (v1/v2/v3 to v4). Identified `OrderService` gaps to fill: partial-fill hook, rejected-protection hook, awaitable cancel confirmation, terminal-order guard in `modify_order`, `sync_from_ib` after reconnect (`attach` is a no-op when repeated), overfill/NEEDS_RECONCILIATION on a second closing fill, and shutdown (`cancel_unprotected_orders` also cancels a working `close` order).

## OptionOmega Plan Revision (2026-11-03)

- `docs/optionomega_bot_plan.md` revised (documentation only, no code changed):
  - Live-only runtime: no `backtest_only` section in the YAML; unknown keys (incl. `backtest_only`, slippage keys) are validation errors. `option_omega_entry_slippage` removed.
  - Slippage is measured, not configured. Entries: `execution.max_adverse_price_change`. Hard-stop market exit: slippage = hoped-for minus received price (nothing to influence). Profit target: price improvement reported. Soft exits (time/delta/deadline) use the same limit chaser starting at mid toward bid (sell) / ask (buy), never beyond it, never market; the soft-exit `execution` block has no price budget. Each order stores a `reference_price` so reporting can derive slippage. OO premium filter now applies to the anchor quote and every submitted limit.
  - Persistence: clean schema v4 without backward compatibility (no relevant DB data yet): no v1-v3 migration code (older `user_version` rejected), `PENDING` renamed `ENTRY_PENDING` with a `CHECK` on status, typed `orders` columns (`oca_group`, `oca_type`, `trigger_method`, `outside_rth`, `account`, `reference_price`) instead of `order_json`, `virtual_positions` drops `legs_data`, `expiration`, `tp_order_id`, `sl_order_id` in favor of `position_legs`. The strategy bot and its tests are adapted to v4 in the same change.
  - Phase 2: new to-be-defined manual close of framework-opened positions via an orderRef convention such as `Close <VPID>` (VPID = `virtual_positions.position_id`); open design points listed in section 7.
  - Phase 3: "external signal providers" removed (DIX/GEX/CSV signals are unsupported).

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
- **Telegram Notification Bot (`bots/telegram/`)**: Implemented bot type for real-time notifications via Telegram. Subscribes to shared IB connection global order events (`openOrderEvent`, `orderStatusEvent`, `execDetailsEvent`), formats HTML alerts with debouncing and account/orderRef filters, and transmits via non-blocking `TelegramClient` (`framework/telegram_client.py`). Sends a comprehensive "connected" readiness status message on startup detailing account balances (Net Liquidation, Total Cash, Available Funds, Buying Power, Margin), active portfolio positions (with cost basis, market price, and unrealized P&L), and open orders. Two-tier secret credentials (`telegram.connections`) added to `SystemConfig` (`framework/config/system_config.py`).
- **Database Inspection Tool**: Created `show_db.py` CLI script to inspect table schemas and active virtual positions.
- **Environment Structure & Multi-Connection Support**:
  - Environments configured: `config/live`, `config/live-read`, `config/demo`, and `config/test-live`.
  - Supports multiple concurrent Interactive Brokers connections ("per account and per user").
  - `config/live` runs user `jensengelke-bot1` connected to IB Gateway on port 4001 via `config-live.ini` (`OverrideTwsApiPort=4001`).
  - `config/live-read` runs user `jensengelke-bot2` connected to dedicated IB Gateway on port 4002 via `config-live-read.ini` (`OverrideTwsApiPort=4002`).
  - `SystemConfig` updated to auto-detect and resolve `ibcIni` (supports both absolute paths and config-directory relative paths) and `twsSettingsPath`.
  - `IBConnectionManager` updated with Windows `/TwsSettingsPath:` support and detailed IBC startup logging.

## IBC Startup Investigation (2026-11-02)

- Verified against local upstream source at `C:\git\ibc`: `IncludeStackTraceForExceptions=yes` in the active `C:\IBC\config-live-read.ini` enables full exception traces on stdout, already captured in the farm's system trace log. No application logging change is needed for these traces.
- Python invokes `scripts\StartIBC.bat` directly, bypassing `DisplayBannerAndLaunch.bat`, which creates the standalone `C:\IBC\logs` files.
- Standalone 1050 logs show bundled Java 25.0.2.0.101; the failing Python launch explicitly uses Semeru/OpenJ9 Java 27. Recommend testing with `ibc.javaPath: ""` to use bundled Java; root cause remains unconfirmed pending a stack trace.
- Exit 1107 is named `CANT_FIND_ENTRYPOINT`, but `startGateway()` uses it for any Throwable from `ibgateway.GWClient.main()`. It does not prove an entrypoint is absent or identify an XML file. No live session was launched or runtime configuration changed during investigation.

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
- `telegram`: Real-time order, fill, and status notification bot with extensible observation architecture.

### Persistence Files
- `data/live_positions.db`: SQLite database for live trading virtual positions.
- `data/demo_positions.db`: SQLite database for demo trading virtual positions.
- `data/test_live_positions.db`: SQLite database for test-live trading virtual positions.

## Notes for Future Sessions
- Memory Bank files are located in `memory-bank/` directory.
- Always read all files in `memory-bank/` at the start of a task.
- Update `activeContext.md` and `progress.md` as work progresses.
