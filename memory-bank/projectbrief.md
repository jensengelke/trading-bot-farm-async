# Project Brief

## Project Name
Trading Bot Farm (Async)

## Project Purpose
A Python-based asynchronous trading bot framework that enables multiple automated options and multi-asset trading bots to run concurrently. The farm provides shared infrastructure, singleton connection pooling to Interactive Brokers (IB Gateway / TWS), automated gateway lifecycle management (IBC / Watchdog), targeted error dispatching, database-backed virtual position tracking, and configurable options strategy execution.

## Core Requirements

### 1. Multi-Bot Architecture & Concurrent Execution
- Support multiple independent bot instances running concurrently in a single asyncio event loop.
- Dynamic bot discovery and instantiation from YAML configuration files located in environment-specific config directories (`config/{instance_name}/*.yaml`).
- Standardized bot lifecycle contract inheriting from `BotBase` with `start()` and `stop()` coroutines and resource cleanup.
- Error isolation ensuring one bot's runtime failure does not crash or interrupt other running bots.

### 2. Singleton IB Connection Pooling & Gateway Lifecycle
- Centralized `IBConnectionManager` providing thread-safe, shared connection management via `ib_async`.
- Single IB client connection shared among all bots to eliminate client ID collisions and minimize socket overhead.
- Automated IB Gateway lifecycle management via IBC and `Watchdog`, supporting automated startup, recovery, and configurable maintenance/trading windows (time/day-based connection scheduling in `America/New_York`).
- Clean connection lifecycle management with graceful shutdown upon system termination signals (SIGINT, SIGTERM).

### 3. Targeted Error Dispatching & Request Tracking
- Thread-safe `RequestTracker` mapping IB API request IDs (`reqId`) to specific originating bot instances.
- Centralized `ErrorDispatcher` routing IB API `errorEvent` notifications exclusively to the originating bot instance, isolating bot log outputs and eliminating duplicate error logs across shared connections.
- Fallback routing of untracked and system-level errors directly to the system logger.

### 4. Virtual Position Persistence & State Decoupling
- SQLite-backed `PositionManager` maintaining virtual positions (`VirtualPosition`) in dedicated instance databases (`data/{instance}_positions.db`).
- Decouples strategy-level spread definitions (e.g., overlapping butterflies or condors sharing strikes) from IBKR's consolidated net portfolio positions.
- Full tracking of position metadata, legs, opening/closing fills, execution prices, timestamps, initial Greeks, and attached bracket order IDs.
- CLI inspection utility (`show_db.py`) for auditing database tables, schema, and active position records.

### 5. Configurable Option Strategy Engine
- Dedicated `bots/strategy/` engine executing complex multi-leg options spreads (Iron Condors, Butterflies, FKK-style Bull Put Spreads, asymmetric risk-free spreads) on indices (SPX) and equities.
- Automated DTE targeting (exact or flexible nearest-expiry matching) and trading class disambiguation (resolving `SPXW` vs `SPX` weekly/monthly options).
- Intelligent strike selection:
  - `underlying_offset`: Relative point offset from current underlying price.
  - `leg_offset`: Relative point offset referencing a parent leg.
  - `delta`: Greeks-based strike selection using `find_option_by_delta` with batched market data requests and alternate strike (OTM/ITM) search logic.
- Dynamic execution algorithm:
  - Configurable mid-price sampling period (`mid_price_monitoring_period`) calculating mean mid-prices.
  - Iterative price chasing via limit order adjustments (`max_price_adjustments`, `price_adjustment_wait_seconds`) stepping by `min_tick` towards fill while respecting `min_premium` and `max_premium` bounds.
- Automated risk management:
  - Bracket orders: Automatic GTC Stop Loss and Take Profit combo orders (supporting execution outside regular trading hours).
  - Entry condition evaluators (`SMA`, `underlying_intraday_move`, `VIX`) short-circuiting entry if market conditions are adverse.
  - Exit condition evaluators (`position_delta`) triggering position closure based on portfolio Greek drift.

### 6. Multi-Environment Configuration System
- Instance-based environment directory isolation: `live`, `demo`, and `test-live`.
- Two-tier system configuration:
  - `config.yaml`: Public environment settings (host, port, client_id, database path, version-controlled).
  - `.secret-config.yaml`: Sensitive credentials (account numbers, Flex tokens, IBC credentials, gitignored).
- Deep merging of public and secret configurations with strict Pydantic schema validation (`ConfigModel`, `ConnectionConfig`, `FlexConfig`, `DatabaseConfig`, `IbcConfig`).
- Dedicated Pydantic schemas for bot configurations (`StrategyBotConfig`, `VerifyBotConfig`).

### 7. Observability & Logging Infrastructure
- Three-tier logging system per component (system and each bot instance):
  - Standard logs (`*.log`, INFO and above)
  - Error logs (`*-error.log`, WARNING and above)
  - Trace logs (`*-trace.log`, DEBUG and above)
- Automated startup log rotation moving previous runs into timestamped backup folders (`backup-YYYYMMDD_HHMMSS/`).
- Method-level execution tracing decorator (`@trace_all_methods`) for rapid debugging.

## Technology Stack
- **Language**: Python 3.12
- **Async Framework**: `asyncio`
- **IB Integration**: `ib_async (>=2.1.0)`, IBC, `Watchdog`
- **Database**: SQLite 3 (via standard library `sqlite3`)
- **Data Validation & Typing**: Pydantic (v2 compatible)
- **Configuration & Utilities**: PyYAML (>=6.0), `pytz`, `tzdata`, `croniter`
- **Platform**: Windows 11 (primary operational environment)

## Project Structure
```
trading-bot-farm-async/
├── trading_bot_farm.py          # Main application entry point & CLI
├── show_db.py                   # SQLite position inspection utility
├── requirements.txt             # Project dependencies
├── framework/                    # Core framework infrastructure
│   ├── bot_base.py              # Abstract base class for bot implementations
│   ├── bot_manager.py           # Bot discovery, instantiation & lifecycle orchestrator
│   ├── ib_connection.py         # Shared IBConnectionManager, IBC & Watchdog lifecycle
│   ├── request_tracker.py       # RequestTracker & ErrorDispatcher for targeted error logs
│   ├── position_manager.py      # SQLite VirtualPosition & PositionManager persistence
│   ├── option_utils.py          # Delta strike search & option analysis helpers
│   ├── entry_conditions.py      # Entry filters (SMA, intraday move, VIX)
│   ├── exit_conditions.py       # Exit filters (position Greeks & delta thresholds)
│   ├── logging_config.py        # Three-tier logging & auto-rotation setup
│   ├── decorators.py            # Method tracing decorators (@trace_all_methods)
│   └── config/
│       └── system_config.py     # Pydantic models & two-tier YAML configuration loader
├── bots/                        # Concrete bot implementations
│   ├── strategy/                # Configurable multi-leg option strategy bot
│   │   ├── __init__.py
│   │   ├── bot.py               # Strategy execution engine & scheduling loop
│   │   └── config.py            # Pydantic configuration schema for strategy bots
│   └── verify/                  # Multi-asset connectivity verification bot
│       ├── __init__.py
│       ├── bot.py               # Historical bar verification (stocks, options, futures)
│       └── config.py            # Pydantic schema for verify bot
├── config/                      # Instance environments
│   ├── demo/                    # Paper/demo trading environment
│   ├── live/                    # Production live trading environment
│   └── test-live/               # Test configuration for live accounts
├── data/                        # SQLite persistence files (*.db)
├── logs/                        # Instance-specific log directories
└── docs/                        # Architectural documentation & strategy guides
```

## Key Design Principles
1. **Singleton Connection, Isolated State**: One shared IB socket connection for efficiency; isolated virtual positions in SQLite to prevent broker-level netting conflicts.
2. **Targeted Observability**: Bot-level request mapping routes error messages exclusively to the bot that originated them.
3. **Fail-Fast Configuration**: Strict Pydantic models validate all environment and bot parameters prior to execution.
4. **Execution Resilience**: Mid-price sampling and gradual price adjustment loops protect against illiquidity and wide bid-ask spreads.
5. **Headless Operational Autonomy**: Automated Gateway restarts and schedule-aware connection windows allow uninterrupted 24/5 server operation.

## Success Criteria
- Multiple option strategy and verification bots run concurrently within a single async event loop without blocking or thread leaks.
- Zero client ID conflicts through the shared `IBConnectionManager`.
- Automated IB Gateway lifecycle managed reliably by IBC and Watchdog during market windows.
- Virtual positions persist reliably across restarts and correctly track opening fills, prices, Greeks, and bracket exits.
- Strategy orders fill effectively near the mid-price via automated tick price adjustments.
- Errors are cleanly dispatched only to the responsible bot, keeping operational logs readable and actionable.
