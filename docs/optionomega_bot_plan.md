# OptionOmega bot: implementation plan

Status: agreed design; runtime implementation not yet started. Updated 2026-11-02.

## 1. Goal and boundaries

Create a new `optionomega` bot, leaving `bots/strategy` and its existing YAML semantics unchanged. Match OptionOmega (OO) backtest configuration wherever a meaningful live equivalent exists. Report differences rather than promising identical fills or results.

Reference: <https://docs.optionomega.com/backtesting/backtest-setup> (including its Markdown version). OO documents positive entry premium bounds for credit and debit strategies, evaluated after entry slippage; slippage is per position, not per leg. Its two-hit backtest rules retrospectively use the first hit's price, which cannot be reproduced live.

No screenshot attachment is available in the current conversation. Examples below are illustrative, not a verified transcription of a screenshot or shared backtest.

### Confirmed user decisions

- Profit target: broker-held **GTC limit** order.
- Hard stop loss: broker-held **GTC stop** order; also allow stop-limit. `order_type` defaults to `stop` if a stop loss is configured. No stop is created merely because this default exists.
- Soft exits (e.g. position delta crossing a threshold): monitored by the farm and executed using a limit chaser.
- Four explicit positive entry premium bounds: minimum/maximum credit and minimum/maximum debit for open.
- Optional OO-prefixed minimum/maximum entry premium inputs reproduce OO's side-agnostic, after-slippage filter. The user accepted fail-fast validation if explicit and OO bounds are both supplied with non-null values, rather than silent precedence.
- No implicit entry schedule, exit rule, profit target, or stop loss. If no exit is specified, hold to expiration, subject to real settlement/assignment behavior.
- First-expiry full-position close is explicitly configured; **false is valid even for mixed-expiry positions**. Some strategies deliberately let selected legs expire.
- Floating entry is Phase 1. Once entry conditions fire, the bot is done entering for that trading day, even if selection, filtering, submission, or filling subsequently fails.

## 2. Pricing vocabulary and units

Use a canonical spread whose leg actions describe opening the strategy. Open by BUYing this canonical BAG; close by SELLing the same BAG. Leg ratios remain positive integers, with separate buy/sell actions.

- `E`: signed opening fill price in the canonical BUY perspective (debit positive, credit negative).
- `M`: signed current value of that same spread, **not** the signed cashflow of its closing transaction.
- Gross P/L per spread in price points: `M - E`.
- Gross P/L in dollars: `(M - E) * multiplier * filled_quantity` (normally multiplier 100).
- Opening premium magnitude: `abs(E)`. Credit/debit is determined by actual net price, not inferred just from the leg pattern.

Configured premiums and fixed P/L amounts are **price points per spread**, not total account dollars. For standard US options 1.00 means $100 per spread. Name the OO-compatible fixed unit `fixed_premium`, not the misleading `fixed_dollars`. Actual commission data belongs in reporting, not in trigger calculations unless an explicit future net-of-fees rule is added.

For percentage `p`:

| Exit | Target signed mark |
| --- | --- |
| Profit target | `E + abs(E) * p / 100` |
| Stop loss | `E - abs(E) * p / 100` |
| Fixed-premium profit/loss amount `x` | `E + x` / `E - x` |
| Positive closing premium `c` | `-c` for credit entry; `+c` for debit entry |

Examples: credit entry -2.00, PT 50% => -1.00; SL 100% => -4.00. Debit entry 2.00, PT 50% => 3.00; SL 50% => 1.00. A closing-premium target must be on the correct profit/loss side of the entry. Validate infeasible debit losses, zero-premium percentage references, tick rounding, and any target that crosses sign; never use `abs(M)` as a general P/L formula.

The IB adapter must test SELL-close price conventions separately from the internal formula, including negative prices and stop trigger direction. Broker stop activation and fills are not guaranteed to use the farm's calculated mid.

## 3. Entry premium filters and execution

All six optional bounds accept non-negative magnitudes; null means absent. Validate min <= max within each pair.

1. If any explicit bound is non-null, use only the explicit family. Both families non-null is a configuration error, as approved.
2. A credit quote is checked against the credit pair; a debit quote against the debit pair. If only credit bounds exist, a debit entry is disallowed, and vice versa. If both pairs exist, either direction may qualify. Phase 1 rejects zero-price entries rather than silently assigning a direction.
3. If no explicit bound is supplied, use the OO-prefixed pair, if present. OO says: "Use positive numbers for both values, regardless of whether the strategy is a credit or a debit" and applies the filter after entry slippage.
4. If neither family is supplied, no premium filter applies.

These are **eligibility filters, never quote clamping**. A quote that fails is skipped, not changed into an eligible synthetic quote.

Explicit bounds also constrain **every submitted entry limit**, including the first attempt and every modification. With BUY-perspective limit `L`, credit bounds imply `-max_credit <= L <= -min_credit`; debit bounds imply `min_debit <= L <= max_debit`, omitting absent bounds. Do not cross from credit to debit during a chase. A limit protects against adverse execution but cannot prevent favorable price improvement beyond a maximum credit/minimum debit bound; report actual fills without claiming such bounds are guaranteed fill constraints.

### Slippage is not a live order surcharge

Keep three concepts separate:

- `backtest_only.slippage`: OO's historical fill assumption. Exit and stop-loss values do not change live trigger prices or orders.
- `entry.option_omega_entry_slippage`: explicit compatibility input used only when OO premium filtering is active. Adverse simulated BUY price is `M + s`: credit premium shrinks, debit premium grows. Reject a zero/sign-crossing result instead of reclassifying the trade. Verify corner cases against an OO reference fixture; the docs do not specify the sign-crossing case.
- `execution.max_adverse_price_change`: a live, explicitly configured chase budget relative to a fixed initial quote anchor. It is never inherited from a backtest setting.

Entries are always limit orders, never market. The chaser may choose a worse limit only within the explicit budget and premium constraints. No fill is guaranteed. Stop-market exits may slip; stop-limit exits bound the limit price but may remain unfilled. Soft exits stay limit-only.

## 4. Proposed YAML contract

This is a design example, not a runnable bot configuration. Store it in documentation until the runtime passes paper validation, not in an auto-discovered environment directory. All values below are explicitly chosen examples, not runtime defaults. Use strict nested Pydantic models (`extra: forbid`) and discriminated condition/selector types. Optional exits may be omitted or null; omission never creates an exit. Execution parameters are required when the associated path is enabled. The one approved behavioral default is hard-stop `order_type: stop`.

```yaml
type: optionomega
name: SPY illustrative calendar
test_mode: true
underlying: {ticker: SPY}
legs:
  - {id: front, action: sell, right: call, qty: 1, dte: 7,
     strike: {type: delta, value: 30}}
  - {id: back, action: buy, right: call, qty: 1, dte: 35,
     strike: {type: offset, parent: front, value: 0, exact: true}}
use_exact_dte: false
round_strikes_to_nearest: null
allocation: {mode: fixed_quantity, quantity: 1, max_open_positions: 1}
entry:
  frequency: {type: weekly, days: [Mon, Thu, Fri]}
  blackout_dates: []
  times: []
  floating_entry: {enabled: true, min_time: '10:00', max_time: '14:00'}
  conditions:
    - {type: underlying_intraday_move, direction: up, unit: percent, threshold: 0.5}
  min_credit_for_open: null
  max_credit_for_open: null
  min_debit_for_open: 1.00
  max_debit_for_open: 3.00
  option_omega_min_entry_premium: null
  option_omega_max_entry_premium: null
  option_omega_entry_slippage: null
  quote_filter: {max_quote_age_seconds: 5, max_leg_bid_ask_width: 0.50}
  execution:
    order_type: limit
    mid_sampling_seconds: 10
    starting_offset: 0.00
    price_increment: 0.01
    wait_seconds: 20
    max_attempts: 5
    max_adverse_price_change: 0.05
profit_target: {type: percent, value: 50}  # broker GTC LMT
stop_loss:
  type: percent
  value: 50
  order_type: stop  # optional; defaults to stop, NOT stop_limit
  trigger_method: default
  stop_limit_offset: null
exit:
  close_full_position_before_first_leg_expires: true
  close_full_position_before_first_leg_expires_minutes: 20
  managed_exits: []
  monitoring: {interval_seconds: 60, align_to_minute: true}
  execution:
    starting_offset: 0.00
    price_increment: 0.01
    wait_seconds: 20
    max_attempts: 5
    max_adverse_price_change: 0.10
backtest_only:
  start_date: 2024-01-01
  end_date: 2025-12-31
  starting_funds: 50000
  commissions: {open_per_contract: 1.65, close_per_contract: 1.65}
  slippage: {entry: 0.05, exit: 0.10, stop_loss: 0.20}
  require_two_consecutive_hits_at_profit_target: true
```

For OO compatibility filtering, set all four explicit bounds to null, then set the OO pair and `option_omega_entry_slippage` (explicitly 0 for none). A future importer may copy a backtest slippage value into this compatibility input, but must show the mapping rather than silently inherit it.

Validation also checks unique leg IDs, acyclic parent links, per-leg DTE, positive quantities, OO delta 0..100 converted to broker units, known trigger methods, stop-limit offset when applicable, allocation consistency, and coherent windows/schedules. Floating mode disallows multiple fixed times and re-entry. The example condition vocabulary is proposed and requires an adapter to the existing evaluator API.

### First-leg expiration policy

Require `close_full_position_before_first_leg_expires` explicitly in the exit-policy block. If true, require a positive integer `close_full_position_before_first_leg_expires_minutes` and exit chaser parameters. If false, minutes may be null and mixed expirations remain valid.

True schedules a full remaining-position close before the earliest leg's **actual last trading cutoff**, adjusted for holidays and early closes, not simply expiration-date midnight. Resolve contract details and exchange sessions, including AM/PM settlement and trading-class distinctions. If a reliable cutoff cannot be resolved, reject entry when this policy is enabled rather than inventing a time. The chaser attempts closure; it cannot guarantee a fill before expiration. Exhausted attempts require an alert and explicit unresolved-risk state, never a false CLOSED status.

False permits leg expiry. Maintain per-leg quantities/status and residual exposure: remove expired legs only after settlement/assignment reconciliation. Do not mark the whole position closed at its first expiry. Cancel obsolete full-combo orders and reconcile any residual orders before replacing them. Assignment can create stock exposure; exercise can consume capital; short American options can be assigned before expiry. Unknown events require operator intervention and block unsafe further trading. Later-dated legs remain tracked. Phase 1 must handle this state safely, even if some residual adjustments require manual intervention.

### Floating entry daily latch

Use New York trading dates and exchange-aware sessions. Re-evaluate conditions during the explicit floating window, at the configured monitoring cadence. Atomically persist `entry_triggered` for `(bot_id, trading_date)` **before** starting strike selection or order submission. The latch survives restart and prevents duplicate attempts after a no-fill, filter rejection, reconnect, or exception. Conditions that remain false do not consume the day. Finish handling an already-submitted attempt, but never start a second attempt that day. Condition evaluation must specify freshness and missing-data behavior (missing/stale data cannot fire).

## 5. OO settings coverage and ignored inputs

Accept recognized backtest-only inputs under `backtest_only`, validate them, and log each present ignored key with its reason at startup. Unknown or unimplemented runtime settings are errors, not silently ignored. A future import report must identify renamed/mapped keys and unsupported features.

| OO setting | Runtime disposition |
| --- | --- |
| Start/end dates, date presets | Ignored: historical test interval |
| Strategy name/template | Metadata; concrete legs define trading |
| Ticker, action/right/quantity, per-leg DTE, exact DTE | Phase 1; broker qualification and trading-class mapping |
| Delta and linked strike offsets/width lock | Phase 1; documented directional rounding |
| Round strikes to nearest | Phase 1 selector constraint |
| % OTM, fixed premium, underlying strike offset | Extend selectors after core Phase 1 |
| Spread premium selection, linked premium/delta with max width | Phase 3 |
| Fixed quantity / max contracts | Phase 1; explicit quantity and risk cap |
| Starting funds | Ignored: actual account equity is used |
| Ignore margin requirements | Ignored: cannot bypass broker buying power |
| Margin allocation %, max allocation, capital per contract | Phase 2 account/margin sizing; reject until implemented |
| Max open positions | Phase 1 durable per-bot count and in-flight reservations |
| Prune oldest | Phase 2 managed close, not an immediate state deletion |
| Fixed entry times, daily/weekly/monthly/specific dates, blackouts | Phase 1 scheduler |
| Floating entry | Phase 1, persisted once-per-trigger-day latch |
| Entry premium min/max | Phase 1 explicit family or OO compatibility family |
| Existing VIX/SMA/intraday-move filters | Phase 1 adapters, subject to data validation |
| Entry short/long ratio | Phase 2 |
| IV, gaps, opening range, additional TA | Phase 2 data/indicator providers |
| DIX/GEX or CSV/custom signals | Unsupported until an explicit provider exists; not inherently backtest-only |
| Re-entry after exit | Later; incompatible with Phase 1 floating daily latch |
| Whole-position PT / SL percent, fixed premium, closing premium | Phase 1 GTC broker exits |
| Early exits DTE/DIT/MIT, position delta, underlying move/tested | Phase 1 managed exit rules with explicit parameters |
| Trailing stop, intra-minute evaluation | Phase 2, subject to broker capability / market-data capacity |
| Leg groups, per-leg stops, profit/time actions | Phase 3 |
| Commissions/fees | Ignored for live order math; actual charges recorded separately |
| Entry/exit/SL slippage assumptions | Ignored as execution settings; explicit OO entry-filter compatibility input is separate |
| Wide bid-ask entry/exit handling | Live quote validation and explicit chase budgets; map OO thresholds only when precise units match |
| Close open trades on test completion | Ignored: no runtime test end |
| Cap non-opening profits at PT | Ignored historical fill modeling; live limit may receive price improvement, not a profit cap |
| Require two consecutive hits at PT | Ignored retrospective fill assumption; broker decides actual fills |
| Cap non-opening stop outs | Ignored: losses cannot be capped by assumption |
| Ignore single-bar SL breach | Ignored retrospective rule; hard stop is broker-triggered |

OO's historical data, minute sampling, margin model, and fills are not the broker's. Matching settings is semantic alignment, not a guarantee of identical trades/results.

## 6. Framework work required

### A. Market data and monitoring

Add a shared, reference-counted `MarketDataService` per IB connection. Maintain subscriptions for underlying, conditions, and all open legs. Provide timestamps, freshness validation, bid/ask/mid, Greeks with source metadata, synchronized spread valuation, reconnect recovery, pacing and subscription budgets. Invalid, crossed, stale or missing quotes cannot trigger entries or quote-derived exits. Existing quote sampling and option selection must use the same service to avoid cancelling another bot's subscription.

Add an exchange-aware `EntryScheduler`, separate from the position monitor. Support explicit fixed schedules and Phase 1 floating windows; no implicit time/window. Persist daily consumption and schedule IDs. Account for New York DST, market holidays, early closes and missed schedules after restart. Do not replay missed entries automatically.

Add `PositionMonitor` for soft exits, expiry deadlines and order-state reconciliation. Keep monitoring existing positions even after the day's floating entry is consumed. Separate condition evaluation from execution; use a per-position lock and durable state transitions to avoid duplicate closes. Define exit priority (broker fills first, then deadline/risk exits, then other soft conditions) and record every reason.

### B. Orders, hard exits and concurrency

Add reusable `LimitChaser` and `BrokerExitOrders` services. Chase using events, partial-fill accounting, tick-aware directional rounding, immutable initial price anchor, explicit timeout/attempt budget and durable order references. A timeout or cancel request is not a confirmed cancellation. Process late fills and reject updates to terminal orders.

Place PT as GTC LMT and SL as GTC STP or STP LMT for confirmed filled quantity. Protect partial entry fills promptly; resizing protection must not accidentally over-close. Where supported, link exits with broker OCA and overfill protection; verify partial-fill reductions instead of assuming cancel-all is adequate. Persist acknowledgment/rejection, permId, account, combo definition, prices, OCA and remaining quantity. Surface a rejected protective order immediately and stop additional entries; never silently convert a requested hard stop into a farm-managed rule.

**Capability gate:** existing `strategy` code submits BAG `StopOrder`s but that is not evidence of IB acceptance. Verify GTC STP/STP LMT, negative-price triggers, combo routing, maximum leg count, calendar support, OCA and trigger methods on the intended contracts/exchanges in a paper account. Public generic stop-order documentation alone does not establish combo support. Some broker-held orders may be IB-simulated rather than exchange-native. If the broker cannot support a requested combination, reject it clearly; do not claim parity or fall back silently. `outsideRth` cannot create an options trading session; align order eligibility with actual sessions and OO RTH semantics.

For a soft/deadline close, lock the position, request cancellation of competing broker exits, wait for definitive acknowledgments, reconcile any fills that raced cancellation, then chase only the remaining quantity. An ambiguous cancellation blocks a competing close and raises an alert. If a chase ends unfilled, preserve explicit unresolved state and restore valid remaining protection where safe; never leave a position silently unprotected. GTC stop-market is not a guaranteed fill price (or guaranteed immediate execution).

### C. Persistence, restart and expiration

Extend `PositionManager` using versioned, tested SQLite migrations. Store canonical legs/conIds/ratios, actual fills and remaining per-leg quantities, multiplier, signed entry price, underlying entry price, individual expiries/cutoffs, broker exit IDs, OCA, soft-exit state, first-expiry policy and execution audit events. Add entry-day latch and in-flight reservations with uniqueness constraints.

Use lifecycle states such as ENTRY_PENDING, OPEN, EXIT_CANCEL_PENDING, EXIT_WORKING, PARTIALLY_EXPIRED, NEEDS_RECONCILIATION, CLOSED. Record idempotent execution IDs so duplicate callbacks cannot double-count fills. CLOSED requires confirmed close/settlement of all exposure, not merely a date passing.

On restart/reconnect: reconcile open orders, recent executions, stored fills and aggregate account positions before permitting entries. Broker net positions cannot identify individual overlapping strategies; aggregate all virtual legs across bots and flag discrepancies, never classify one bot as missing solely because its legs net against another bot. Preserve valid GTC orders; do not duplicate an order just because the local in-memory trade is missing. Resolve gaps outside available execution history using account statements/Flex or operator review. Daily latches survive all recovery paths.

For deliberate leg expiry, cancel stale combo protection before the leg becomes untradeable, reconcile settlement/assignment, and only rebuild protection on a well-defined remaining position. Do not blindly apply original percentage targets to a changed leg set. Phase 1 retains exposure and requests intervention if residual cost basis/order eligibility cannot be determined safely. This is supported configuration with explicit operational limitations, not a validation error.

### D. Selection, sizing and orchestration

Extend shared option utilities for per-leg expirations, exact/nearest DTE, linked strikes, safe trading-class mapping and contract-specific ticks/multipliers. OO backtests SPX PM expirations; do not accidentally select standard AM-settled SPX contracts. Validate the broker's supported leg count independently of OO's eight-leg schema limit.

An `AccountService` provides account-scoped equity, buying power and what-if margin estimates for Phase 2 sizing. Never represent fixed quantity as ignoring real margin requirements. Add concurrency-safe risk reservations across bots.

Create `bots/optionomega/{__init__.py,config.py,bot.py}` as a thin orchestrator using existing BotBase, shared IBConnectionManager and targeted RequestTracker/ErrorDispatcher conventions. Do not subscribe each bot directly to global IB errors. Implement cancellation-safe startup/shutdown, remove event subscriptions, release data references and preserve acknowledged GTC exits. Farm downtime disables soft exits and deadlines even though GTC orders remain; surface this limitation prominently.

### Existing framework reality

Inspection of `bots/strategy/bot.py` finds PositionManager and ExitConditionEvaluator construction, but no position-manager method calls or active exit-evaluation loop. The memory bank's older descriptions overstate runtime integration. These services are useful foundations, not completed monitoring/persistence for the new bot. Existing strategy behavior is not changed as part of this plan.

## 7. Implementation sequence and acceptance gates

### Phase 1: core runtime, including floating entry

1. Save this specification; implement strict schema, ignored-key report, pure pricing/premium-filter functions, and documentation fixtures.
2. Prove broker combo GTC PT/STP/STP LMT and OCA compatibility in paper, or clearly block unsupported configurations. Do this before enabling entry for configurations requiring hard exits.
3. Add durable lifecycle/leg/event storage and the entry-day latch with migration/recovery tests.
4. Build shared market data, per-leg selection and session-aware scheduler (fixed plus floating).
5. Implement bounded limit chaser, protective broker orders and race-safe exit coordination.
6. Add monitoring for explicit soft rules, first-expiry deadlines, and safe partial-expiry handling; wire orchestrator and reconciliation.
7. Only then add a disabled/paper deployment example. Never enable a live strategy or submit orders as part of offline tests.

Phase 1 includes fixed-quantity sizing, per-leg DTE, delta/linked-offset selection, filters, floating entry, broker hard exits, managed risk/time exits, and restart safety. Acceptance requires tests for:

- Credit/debit PT/SL in each unit; tick rounding and SELL-close adapter signs.
- All four bounds, missing pairs, OO after-slippage filter, mutually exclusive families, zero/sign-crossing prices, no quote clamping, chase bounds and favorable fills.
- Hard-stop default only when a stop exists; explicit no-exit means no exit; false first-expiry policy accepted for calendars.
- Floating latch consumed before selection; no retry after rejection/no-fill/error; persistence across restart and New York date/DST rollover.
- Stale data, missing Greeks, disconnected feeds and subscription sharing.
- Partial entry/exit fills, OCA reduction, cancel/fill races, duplicate executions, rejected protection and failed chase protection recovery.
- Early closes/holidays and correct expiry cutoff; failed deadline close; deliberate first-leg expiry with remaining legs tracked; assignment requiring intervention.
- Offline bracket fills, orphaned/missing orders, shared-leg netting across bots and migration compatibility.

Use the existing standard-library `unittest` / `IsolatedAsyncioTestCase` patterns and mocked IB; no new test library is needed. Paper tests are separate, explicitly authorized broker operations, not part of automated discovery tests.

### Phase 2

Margin/capital sizing; additional indicators and entry selectors; short/long ratios; trailing behavior; exit liquidity controls; prune-oldest; supported re-entry modes; higher-frequency monitoring with capacity checks. Each needs explicit schema/execution semantics and tests.

### Phase 3

Leg groups, spread-premium targeting, per-leg stop behavior, profit/time actions, external signal providers, import/migration tooling and a complete supported-settings reference.

## 8. Delivery status

This task records the agreed plan and updates project memory. No new bot, schema, framework service, demo configuration or trading order is implemented/enabled by this document. Next implementation milestone is Phase 1 step 1, with broker capability validation as a release gate.
