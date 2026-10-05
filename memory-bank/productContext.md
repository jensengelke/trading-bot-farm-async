# Product Context

## Why This Project Exists

The Trading Bot Farm framework was created to solve the challenges of running multiple automated options trading strategies concurrently on Interactive Brokers while maintaining strict separation of concerns, high execution quality, reliable persistence, and operational efficiency.

### Problems It Solves

1. **Multi-Strategy Options Execution**: Running diverse option strategies (Butterflies, Iron Condors, Bull Put Spreads, custom multi-leg structures) simultaneously without spawning multiple Python processes or managing separate broker connections.
2. **The Broker Net Portfolio Netting Dilemma**:
   - Interactive Brokers consolidates positions at the contract level. If Strategy A sells two SPX 7520 Calls and Strategy B buys two SPX 7520 Calls, IBKR reports a net position of 0 contracts.
   - At the broker level, individual strategy identity, entry fills, and profit targets are erased.
   - The framework solves this by decoupling strategy state into an internal SQLite database (`PositionManager` / `VirtualPosition`), preserving strategy leg definitions, initial Greeks, fill prices, and bracket orders independently of broker netting.
3. **Execution Quality in Illiquid Option Combos**:
   - Market orders on multi-leg option spreads suffer massive slippage across wide bid-ask spreads.
   - The framework samples mid-prices across all combo legs over a configurable monitoring window (`mid_price_monitoring_period`) to establish fair value, then enters limit orders with dynamic price adjustments (`max_price_adjustments`), stepping by minimum tick increments (`min_tick`) towards execution without crossing into adverse premium territory (`min_premium`, `max_premium`).
4. **Targeted Observability on Shared Sockets**:
   - On a shared IB connection, standard IB API error callbacks broadcast to every listening client.
   - The framework maps request IDs (`reqId`) to originating bots via `RequestTracker` and routes errors via `ErrorDispatcher`, eliminating duplicate log noise and keeping strategy logs clean and isolated.
5. **Headless 24/5 Gateway Lifecycle**:
   - Interactive Brokers TWS/Gateway requires daily maintenance reboots and session refreshes.
   - IBC and `Watchdog` integration automates login, restarts, and connection windows during market hours (`America/New_York`), allowing completely unattended server deployments.
6. **Environment & Credential Isolation**:
   - Separate configuration environments (`config/live`, `config/demo`, `config/test-live`) ensure zero cross-contamination between testing, demo simulations, and live capital.
   - Two-tier YAML configurations keep sensitive passwords, account numbers, and Flex tokens gitignored.

## How It Should Work

### User Workflow

1. **Environment Setup**: User chooses an environment directory (`config/live/`, `config/demo/`, or `config/test-live/`).
2. **System Configuration**:
   - Technical settings in `config.yaml` (host, port, client_id, SQLite database path `data/{env}_positions.db`).
   - Credentials in `.secret-config.yaml` (account ID, Flex Web service credentials, IBC credentials).
3. **Bot Configuration**:
   - User creates or modifies YAML files for each bot instance (e.g., `strategy_30DTE_butterfly.yaml`, `strategy_fkk.yaml`, `verify_options.yaml`).
   - Strategy bots configure underlying contracts (e.g., SPX Index with SPXW weekly options), DTE, scheduled entry days and times, multi-leg structures, delta-based strikes, mid-price monitoring, and bracket orders.
4. **Execution**:
   - User starts the farm: `python trading_bot_farm.py --config config/live` (or `config/demo`).
5. **Runtime Operation**:
   - `BotManager` discovers all bot YAML files, initializes `IBConnectionManager`, and creates tasks.
   - `IBConnectionManager` creates or connects to IB Gateway, optionally launching Watchdog/IBC if enabled.
   - Bots calculate scheduled execution times in `America/New_York` timezone and sleep until triggers fire.
   - At execution time, entry conditions (SMA, intraday move, VIX) are evaluated; if conditions pass, options strikes are resolved (via delta search or strike offsets) and qualified.
   - Orders are placed as combo contracts (`BAG`), monitored, and iteratively adjusted towards fills.
   - Upon execution, virtual positions are persisted in SQLite, and GTC bracket orders (Take Profit / Stop Loss) are placed with unique `orderRef` tags.
   - Exit conditions (such as Greek delta thresholds) periodically monitor open virtual positions.
6. **Position Inspection**:
   - Operator inspects database state using `python show_db.py data/live_positions.db`.
7. **Shutdown**:
   - Operator issues SIGINT (Ctrl+C). The framework cancels active opening limit orders while preserving filled bracket orders (Take Profit / Stop Loss), closes SQLite connections, and disconnects the shared IB socket cleanly.

### Key Operational Behaviors

- **Virtual Position Persistence**: Decouples active strategy trades from IB portfolio consolidation, enabling multiple overlapping spreads with independent profit-taking logic.
- **Shared Connection Singleton**: All bots share a single TCP socket and client ID, preventing gateway reconnect churn.
- **Targeted Error Dispatching**: `RequestTracker` captures `reqId` to ensure warnings/errors appear only in the log of the bot that originated the API call.
- **Gateway Autonomous Maintenance**: IBC Watchdog supervises connection health during scheduled trading hours, reconnecting automatically across network drops or nightly maintenance windows.
- **Fail-Safe Shutdown**: Bot stop preserves GTC bracket orders while cleaning up in-flight limit chase orders and unregistering error callbacks.

## User Experience Goals

### For Option Strategy Traders & Developers
- **Modular Leg Definition**: Define multi-leg strategies cleanly in YAML with intuitive strike selection (`underlying_offset`, `leg_offset`, or `delta`).
- **Rich Context & Abstraction**: Inherit from `BotBase`, access validated Pydantic models, query shared IB connections without socket handling, and utilize helper utilities (`find_option_by_delta`, `EntryConditionEvaluator`, `PositionManager`).
- **Debugging Clarity**: Three-tier logging (`standard`, `error`, `trace`) combined with method tracing (`@trace_all_methods`) makes tracking asynchronous callbacks and order fills trivial.

### For Bot Operators
- **Declarative Configuration**: Update trading schedules, contract ratios, or profit factors in YAML without altering code.
- **Multi-Environment Safety**: Distinct `live`, `demo`, and `test-live` directories prevent accidental live order placement.
- **Auditability**: SQLite databases record every virtual position created, filled, or closed, inspectable anytime via `show_db.py`.

### For System Administrators
- **Unattended Execution**: Headless deployment with IBC and Watchdog handles connection maintenance windows and gateway reboots.
- **Security First**: All passwords, accounts, and credentials live strictly in `.secret-config.yaml` (gitignored).
- **Log Archiving**: Auto-rotation archives previous sessions into timestamped backups on startup.

## Design Philosophy

The framework follows these core principles:

1. **Convention over Configuration**: Sensible defaults and clear naming conventions reduce configuration burden
2. **Fail Fast**: Configuration validation happens at startup, not during trading
3. **Explicit is Better**: Clear separation between system config, bot config, and bot implementation
4. **Async by Default**: Built on asyncio for efficient concurrent operations
5. **Developer Friendly**: Rich logging, type hints, and clear abstractions make development pleasant
