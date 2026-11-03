# OptionOmega bot: implementation plan

Status: agreed design; runtime implementation not yet started. Updated 2026-11-03 (live-only scope, slippage measured instead of configured, clean schema v4, manual-close feature, phase changes). Earlier revision 2026-11-02 after the persistence-framework commit `3fcc5f0` (`OrderService`, schema v3, export/import); see section 6, "Persistence baseline".

## 1. Goal and boundaries

Create a new `optionomega` bot, leaving `bots/strategy` and its existing YAML semantics unchanged. Match OptionOmega (OO) backtest configuration wherever a meaningful live equivalent exists. Report differences rather than promising identical fills or results. The bot is a live runtime only, with no ambition to backtest: its YAML contains no backtest settings.

Reference: <https://docs.optionomega.com/backtesting/backtest-setup> (including its Markdown version). OO documents positive entry premium bounds for credit and debit strategies, evaluated after entry slippage; slippage is per position, not per leg. Its two-hit backtest rules retrospectively use the first hit's price, which cannot be reproduced live. OO simulates slippage; this bot does not configure it, see section 3.

No screenshot attachment is available in the current conversation. Examples below are illustrative, not a verified transcription of a screenshot or shared backtest.

### Confirmed user decisions

- Profit target: broker-held **GTC limit** order.
- Hard stop loss: broker-held **GTC stop** order; also allow stop-limit. `order_type` defaults to `stop` if a stop loss is configured. No stop is created merely because this default exists.
- Soft exits (e.g. position delta crossing a threshold): monitored by the farm and executed using a limit chaser.
- Four explicit positive entry premium bounds: minimum/maximum credit and minimum/maximum debit for open.
- Optional OO-prefixed minimum/maximum entry premium inputs reproduce OO's side-agnostic filter on positive magnitudes. OO applies it after simulated slippage; live, it is applied to the anchor quote and to every submitted limit (section 3). The user accepted fail-fast validation if explicit and OO bounds are both supplied with non-null values, rather than silent precedence.
- No implicit entry schedule, exit rule, profit target, or stop loss. If no exit is specified, hold to expiration, subject to real settlement/assignment behavior.
- First-expiry full-position close is explicitly configured; **false is valid even for mixed-expiry positions**. Some strategies deliberately let selected legs expire.
- Floating entry is Phase 1. Once entry conditions fire, the bot is done entering for that trading day, even if selection, filtering, submission, or filling subsequently fails.
- Live runtime only: the YAML has no `backtest_only` section and no backtest settings.
- Slippage is measured after the fact, never configured. Entries are bounded by `execution.max_adverse_price_change`. The slippage of a hard-stop market exit is the difference between the expected and the received price. Soft exits use a limit chaser that starts at mid and approaches the bid (selling) or ask (buying).
- The database schema is redesigned cleanly as v4. There is no relevant data to preserve, so the optimal design wins over compatibility with the v1-v3 layouts.

## 2. Pricing vocabulary and units

Use a canonical spread whose leg actions describe opening the strategy. Open by BUYing this canonical BAG; close by SELLing the same BAG. Leg ratios remain positive integers, with separate buy/sell actions.

- `E`: signed opening fill price in the canonical BUY perspective (debit positive, credit negative).
- `M`: signed current value of that same spread, **not** the signed cashflow of its closing transaction.
- Gross P/L per spread in price points: `M - E`.
- Gross P/L in dollars: `(M - E) * multiplier * filled_quantity` (normally multiplier 100).
- Opening premium magnitude: `abs(E)`. Credit/debit is determined by actual net price, not inferred just from the leg pattern.

Configured premiums and fixed P/L amounts are **price points per spread**, not total account dollars. For standard US options 1.00 means $100 per spread. Name the OO-compatible fixed unit `fixed_premium`, not the misleading `fixed_dollars`. Actual commission data belongs in reporting, not in trigger calculations unless an explicit future net-of-fees rule is added. The framework already stores each execution's commission and realized P/L in `fills` (`commission`, `commission_currency`, `realized_pnl`); reporting reads them from there.

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
3. If no explicit bound is supplied, use the OO-prefixed pair, if present. OO says: "Use positive numbers for both values, regardless of whether the strategy is a credit or a debit" and applies the filter after its simulated entry slippage. This bot has no simulated slippage, so the pair is checked against the anchor quote and against every submitted limit (below).
4. If neither family is supplied, no premium filter applies.

These are **eligibility filters, never quote clamping**. A quote that fails is skipped, not changed into an eligible synthetic quote.

Explicit bounds also constrain **every submitted entry limit**, including the first attempt and every modification. With BUY-perspective limit `L`, credit bounds imply `-max_credit <= L <= -min_credit`; debit bounds imply `min_debit <= L <= max_debit`, omitting absent bounds. Do not cross from credit to debit during a chase. A limit protects against adverse execution but cannot prevent favorable price improvement beyond a maximum credit/minimum debit bound; report actual fills without claiming such bounds are guaranteed fill constraints. The OO pair constrains every submitted limit the same way, as side-agnostic magnitudes: `min <= abs(L) <= max`, and `L` must not change sign relative to the anchor quote.

### Slippage is measured, not configured

Slippage has no configuration key. The runtime YAML contains no simulated-fill setting and nothing is inherited from a backtest. Each order type deals with the difference between expected and actual price in its own way:

- **Entries:** OO's backtest slippage is replaced by `execution.max_adverse_price_change`, a live, explicitly configured chase budget relative to a fixed initial quote anchor (the mid price when the entry attempt starts). The chaser may choose a worse limit only within this budget and the premium constraints above. Entries are always limit orders, never market, and no fill is guaranteed.
- **Hard stop-loss exits:** a triggered stop becomes a market order (stop-limit: a limit order). Slippage is the delta between the price the farm hoped for (the stop's calculated trigger price) and the price actually received. Nothing can be configured or influenced; it is measured and reported. A stop-limit bounds the limit price but may remain unfilled.
- **Profit target:** a broker-held limit order fills at the limit or better. Price improvement is reported as a favorable difference.
- **Soft exits** (time exits, deadline closes, position-delta changes and other managed rules): executed with the same limit chaser as entries. The first limit is the current mid price of the canonical spread. The chaser then approaches the bid when selling (closing is a SELL of the canonical BAG) or the ask when buying, in `price_increment` steps every `wait_seconds`, for at most `max_attempts`. The limit never goes beyond the natural side (bid or ask), so a soft exit is never a market order and may remain unfilled. An unfilled exit leads to the unresolved-risk state of section 6B.

Measurement: every order stores its reference price (the price the farm hoped for: the entry's initial anchor mid, the stop's trigger price, the profit target's limit price, the soft exit's initial mid) next to the actual average fill price (`orders.avg_fill_price`, `fills`). Reporting derives slippage as the signed difference, positive when adverse, in price points per spread and in dollars. Slippage never feeds back into triggers, filters or orders.

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
  execution:  # soft exits: limit chaser from mid toward bid (selling) / ask (buying)
    price_increment: 0.01
    wait_seconds: 20
    max_attempts: 5
```

For OO compatibility filtering, set all four explicit bounds to null and set only the OO pair. The runtime YAML has no backtest or slippage section: unknown keys, including `backtest_only`, are validation errors (`extra: forbid`). A future OO importer may propose `execution.max_adverse_price_change` from an OO entry slippage value, but must show that mapping rather than silently inherit it. The soft-exit `execution` block has no price budget: the chase starts at mid, moves by `price_increment` toward the natural side (bid when selling, ask when buying) and stops there.

Validation also checks unique leg IDs, acyclic parent links, per-leg DTE, positive quantities, OO delta 0..100 converted to broker units, known trigger methods, stop-limit offset when applicable, allocation consistency, and coherent windows/schedules. Floating mode disallows multiple fixed times and re-entry. The example condition vocabulary is proposed and requires an adapter to the existing evaluator API.

### First-leg expiration policy

Require `close_full_position_before_first_leg_expires` explicitly in the exit-policy block. If true, require a positive integer `close_full_position_before_first_leg_expires_minutes` and exit chaser parameters. If false, minutes may be null and mixed expirations remain valid.

True schedules a full remaining-position close before the earliest leg's **actual last trading cutoff**, adjusted for holidays and early closes, not simply expiration-date midnight. Resolve contract details and exchange sessions, including AM/PM settlement and trading-class distinctions. If a reliable cutoff cannot be resolved, reject entry when this policy is enabled rather than inventing a time. The chaser attempts closure; it cannot guarantee a fill before expiration. Exhausted attempts require an alert and explicit unresolved-risk state, never a false CLOSED status.

False permits leg expiry. Maintain per-leg quantities/status and residual exposure: remove expired legs only after settlement/assignment reconciliation. Do not mark the whole position closed at its first expiry. Cancel obsolete full-combo orders and reconcile any residual orders before replacing them. Assignment can create stock exposure; exercise can consume capital; short American options can be assigned before expiry. Unknown events require operator intervention and block unsafe further trading. Later-dated legs remain tracked. Phase 1 must handle this state safely, even if some residual adjustments require manual intervention.

### Floating entry daily latch

Use New York trading dates and exchange-aware sessions. Re-evaluate conditions during the explicit floating window, at the configured monitoring cadence. Atomically persist `entry_triggered` for `(bot_id, trading_date)` (table `entry_latches`, section 6C; keyed by `bot_id`, never by config version) **before** starting strike selection or order submission. The latch survives restart and prevents duplicate attempts after a no-fill, filter rejection, reconnect, or exception. Conditions that remain false do not consume the day. Finish handling an already-submitted attempt, but never start a second attempt that day. Condition evaluation must specify freshness and missing-data behavior (missing/stale data cannot fire).

## 5. OO settings coverage and ignored inputs

The runtime YAML has no section for backtest-only settings: the bot is live-only, and unknown or unimplemented settings are validation errors, never silently ignored. The table maps each OO setting to its runtime disposition. "Ignored" means there is no runtime equivalent and the key is not accepted in the YAML. A future import report must identify renamed/mapped keys and unsupported features.

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
| Max open positions | Phase 1 durable per-`bot_id` count of `virtual_positions` in ENTRY_PENDING or OPEN (ENTRY_PENDING is the in-flight reservation, written before the order is sent); independent of config version |
| Prune oldest | Phase 2 managed close, not an immediate state deletion |
| Manual close of a framework-opened position | Phase 2, to be defined (section 7) |
| Fixed entry times, daily/weekly/monthly/specific dates, blackouts | Phase 1 scheduler |
| Floating entry | Phase 1, persisted once-per-trigger-day latch |
| Entry premium min/max | Phase 1 explicit family or OO compatibility family |
| Existing VIX/SMA/intraday-move filters | Phase 1 adapters, subject to data validation |
| Entry short/long ratio | Phase 2 |
| IV, gaps, opening range, additional TA | Phase 2 data/indicator providers |
| DIX/GEX or CSV/custom signals | Unsupported; no external signal provider is planned |
| Re-entry after exit | Later; incompatible with Phase 1 floating daily latch |
| Whole-position PT / SL percent, fixed premium, closing premium | Phase 1 GTC broker exits |
| Early exits DTE/DIT/MIT, position delta, underlying move/tested | Phase 1 managed exit rules with explicit parameters |
| Trailing stop, intra-minute evaluation | Phase 2, subject to broker capability / market-data capacity |
| Leg groups, per-leg stops, profit/time actions | Phase 3 |
| Commissions/fees | Ignored for live order math; actual charges recorded separately by `OrderService` in `fills.commission` |
| Entry/exit/SL slippage assumptions | Ignored: slippage is measured after the fact, not configured; entries use `execution.max_adverse_price_change`, soft exits a limit chaser from mid toward bid/ask (section 3) |
| Wide bid-ask entry/exit handling | Live quote validation and explicit chase budgets; map OO thresholds only when precise units match |
| Close open trades on test completion | Ignored: no runtime test end |
| Cap non-opening profits at PT | Ignored historical fill modeling; live limit may receive price improvement, not a profit cap |
| Require two consecutive hits at PT | Ignored retrospective fill assumption; broker decides actual fills |
| Cap non-opening stop outs | Ignored: losses cannot be capped by assumption |
| Ignore single-bar SL breach | Ignored retrospective rule; hard stop is broker-triggered |

OO's historical data, minute sampling, margin model, and fills are not the broker's. Matching settings is semantic alignment, not a guarantee of identical trades/results.

## 6. Framework work required

### Persistence baseline (commit `3fcc5f0`)

Already implemented and unit-tested (`tests/test_order_service.py`, `tests/test_db_transfer.py`; 56 tests pass). Reuse it; do not rebuild it:

- `OrderService` (`self.order_service` on `BotBase`, one instance per database file). Bots call `create_position`, `place_order(ib, position_id, contract, order, role)`, `modify_order`, `cancel_order` and `cancel_unprotected_orders`. They never call `ib.placeOrder`/`ib.cancelOrder`, set `orderRef` or track trades. Roles: `open`, `takeprofit`, `stoploss`, `close`.
- `orderRef` is framework-assigned: `<bot_id>:<position_id>:<role>`, with a `#n` suffix when a role repeats (`bot_id` = YAML file stem). Events for unknown refs, such as manual TWS orders, are ignored.
- Event-driven persistence (`orderStatusEvent`, `execDetailsEvent`, `commissionReportEvent`, `errorEvent`); `attach()` also replays `ib.trades()`. IB rejection reasons are stored in `orders.status_message`/`error_code`.
- Schema v3 in `framework/db_schema.py`: `bots` (config-hash `version_id`), `virtual_positions`, `orders`, `fills` (PK `exec_id`), `PRAGMA user_version`, structural idempotent migration of older layouts.
- Export/import (`db_tool.py`, `framework/db_transfer.py`) merges databases by natural keys in one transaction.

Gaps against this plan (the sections named in the first column handle each):

| Plan need | Baseline | Gap |
| --- | --- | --- |
| Lifecycle (6C) | `virtual_positions.status` is PENDING, OPEN, CLOSED or CANCELLED; the closing transition only fires from OPEN | Additional states and transition guards |
| Legs and expiries (6C) | `legs_data` JSON blob; one NOT NULL `expiration` column | `position_legs` table with per-leg quantity, expiry and cutoff, replacing both |
| Entry latch, reservations (4, 6C) | None. A PENDING position is written before the order is sent; `get_open_positions` returns OPEN only | `entry_latches` table; ENTRY_PENDING+OPEN count |
| Order detail (6B) | `orders` keeps ids, `perm_id`, `client_id`, type, tif, quantity, prices, `contract_json`, status, rejection reason, filled quantity | Typed columns for OCA group/type, trigger method, `outsideRth`, account (only `fills` has it) and the reference price for slippage measurement |
| Audit trail (6B, 6C) | Latest prices plus `modify_count`; no status or modification history | `order_events` table |
| Hard-exit OCA (6B) | None; after a closing fill, `_cancel_remaining_orders` cancels the sibling orders | Broker OCA; keep cancel-after-fill only as a fallback |
| Cancel/modify safety (6B) | `cancel_order` only sends the request; `modify_order` does not check the order status | Awaitable cancel confirmation; terminal-order guard |
| Protection rejected (6B) | Reason persisted, position stays OPEN, nobody is notified | Listener/flag hook that blocks further entries |
| Reconnect (6C) | `attach()` returns early when called again, so replay happens once per process | Explicit `sync_from_ib` after each reconnect |
| Expiry, exercise, assignment (6C) | Not captured: they carry no framework `orderRef` | Position/account reconciliation |
| Shutdown (6D) | `cancel_unprotected_orders` cancels working `close` orders and keeps take-profit/stop-loss | A shutdown during a soft-exit chase must not strand an unprotected position |

### A. Market data and monitoring

Add a shared, reference-counted `MarketDataService` per IB connection. Maintain subscriptions for underlying, conditions, and all open legs. Provide timestamps, freshness validation, bid/ask/mid, Greeks with source metadata, synchronized spread valuation, reconnect recovery, pacing and subscription budgets. Invalid, crossed, stale or missing quotes cannot trigger entries or quote-derived exits. Existing quote sampling and option selection must use the same service to avoid cancelling another bot's subscription.

Add an exchange-aware `EntryScheduler`, separate from the position monitor. Support explicit fixed schedules and Phase 1 floating windows; no implicit time/window. Persist daily consumption and schedule IDs. Account for New York DST, market holidays, early closes and missed schedules after restart. Do not replay missed entries automatically.

Add `PositionMonitor` for soft exits, expiry deadlines and order-state reconciliation. Keep monitoring existing positions even after the day's floating entry is consumed. Separate condition evaluation from execution; use a per-position lock and durable state transitions to avoid duplicate closes. Define exit priority (broker fills first, then deadline/risk exits, then other soft conditions) and record every reason.

### B. Orders, hard exits and concurrency

Add reusable `LimitChaser` and `BrokerExitOrders` services as thin layers over the existing `OrderService` (`place_order`, `modify_order`, `cancel_order`); they never call `ib.placeOrder`/`ib.cancelOrder` and never set `orderRef`. The same `LimitChaser` serves entries (from mid, within `max_adverse_price_change`) and soft/deadline exits (from mid toward the bid when selling or the ask when buying, never beyond it). Chase using events, partial-fill accounting, tick-aware directional rounding, immutable initial price anchor, explicit timeout/attempt budget and durable order references (the framework `orderRef`, with its `#n` suffix when a role is re-placed). A timeout or cancel request is not a confirmed cancellation: `OrderService.cancel_order` only sends the request and the final status arrives later through the status event, so add an awaitable confirmation. Process late fills and reject updates to terminal orders (`modify_order` does not check the status today).

Place PT as GTC LMT and SL as GTC STP or STP LMT for confirmed filled quantity. Protect partial entry fills promptly; resizing protection must not accidentally over-close. Where supported, link exits with broker OCA and overfill protection; verify partial-fill reductions instead of assuming cancel-all is adequate. The existing `OrderService._cancel_remaining_orders` is not OCA: between the first closing fill and the cancel, a sibling order can also fill. Treat it as a fallback, not as the protection. Persist acknowledgment/rejection, permId, account, combo definition, prices, OCA and remaining quantity. `orders` already holds status, rejection `status_message`/`error_code`, `perm_id`, `client_id`, `contract_json`, prices and `filled_quantity`; OCA group/type, trigger method, `outsideRth` and account still have to be added as typed columns (section 6C), together with the reference price used to measure slippage (section 3), and per-modification history belongs in the audit events. Surface a rejected protective order immediately and stop additional entries (today the reason is persisted but the position stays OPEN and nothing notifies the bot, so `OrderService` needs a listener hook); never silently convert a requested hard stop into a farm-managed rule.

**Capability gate:** existing `strategy` code submits BAG `StopOrder`s but that is not evidence of IB acceptance. Verify GTC STP/STP LMT, negative-price triggers, combo routing, maximum leg count, calendar support, OCA and trigger methods on the intended contracts/exchanges in a paper account. Public generic stop-order documentation alone does not establish combo support. Some broker-held orders may be IB-simulated rather than exchange-native. If the broker cannot support a requested combination, reject it clearly; do not claim parity or fall back silently. `outsideRth` cannot create an options trading session; align order eligibility with actual sessions and OO RTH semantics.

For a soft/deadline close, lock the position, request cancellation of competing broker exits, wait for definitive acknowledgments, reconcile any fills that raced cancellation, then chase only the remaining quantity. An ambiguous cancellation blocks a competing close and raises an alert. If a chase ends unfilled, preserve explicit unresolved state and restore valid remaining protection where safe; never leave a position silently unprotected. GTC stop-market is not a guaranteed fill price (or guaranteed immediate execution). The close order of a soft or deadline exit uses the `close` role. `OrderService` marks the position CLOSED when a take-profit, stop-loss or close order fills, and ignores a second closing fill after that because it only acts on OPEN positions. A racing second fill must instead be detected as an overfill and move the position to NEEDS_RECONCILIATION.

### C. Persistence, restart and expiration

Redesign the existing schema (`framework/db_schema.py`, `PositionManager`, `OrderService`) cleanly as schema v4. Do not add a second persistence path. No relevant database data exists yet, so the optimal design wins over compatibility: no backfill, no additive-only constraint, and no in-place upgrade of v1-v3 files. Store canonical legs/conIds/ratios, actual fills and remaining per-leg quantities, multiplier, signed entry price, underlying entry price, individual expiries/cutoffs, broker exit IDs, OCA, soft-exit state, first-expiry policy and execution audit events. Add entry-day latch and in-flight reservations with uniqueness constraints.

**Schema rules** (`SCHEMA_VERSION` becomes 4):

- One clean v4 DDL (`CREATE TABLE IF NOT EXISTS`) creates every table, with the constraints the v3 migration path made impractical (a `CHECK` on `virtual_positions.status`, `NOT NULL` wherever the model requires it). The structural v1-v3 migration code and its tests are removed. A database file with an older `PRAGMA user_version` (including the strategy bot's current files) is rejected on open with a clear message to archive or delete it; it is never altered in place.
- Every new table needs a **natural primary key** that is identical on every machine (UUIDs, orderRefs, IB `execId`, `bot_id` plus date), never an autoincrement id, and an `updated_at` column. `db_transfer` merges rows by natural key, resolves conflicts by `updated_at`, and handles `bots` and `fills` specially.
- Register every new table in `db_transfer.TABLES`, parents before children, and bump `FORMAT_VERSION` because a file without the new tables is no longer complete. Use `INSERT OR IGNORE` for append-only data. Decide per table whether it is immutable (like `fills`) or last-writer-wins (like `orders`).
- Config versioning: `bots.version_id` hashes the whole YAML (`canonical_config_json`, with `default=str` for dates). Any YAML edit creates a new bot version. Everything that must outlive a config edit therefore keys on `bot_id`, not `bot_version_id`: the entry latch, the max-open-positions count and recovery. The `bots` row stays a snapshot of the configuration a position was opened with.

Planned additions:

| Table | Natural key | Content |
| --- | --- | --- |
| `entry_latches` | `(bot_id, trading_date)` | `triggered_at`, trigger reason, optional `position_id`; insert-only, so an insert that hits the primary key means "already consumed" |
| `position_legs` | `(position_id, leg_id)` | `con_id`, ratio, right/strike, `expiry`, `last_trade_cutoff`, filled and remaining quantity, leg status (OPEN, EXPIRED, EXERCISED, ASSIGNED, CLOSED) |
| `order_events` | UUID `event_id` | `order_ref`, event type (status, modification, cancel request, error), prices, status, timestamp; append-only audit trail |

Changes to existing tables (made directly in the v4 DDL, not as `ALTER TABLE`): `orders` gains the typed columns `oca_group`, `oca_type`, `trigger_method`, `outside_rth`, `account` and `reference_price` (the price the farm hoped for, used to measure slippage, section 3) instead of a JSON snapshot, so reporting can query them. `virtual_positions` gains the signed entry price, multiplier, underlying entry price, first-expiry policy and soft-exit state, and loses `legs_data`, `expiration`, `tp_order_id` and `sl_order_id`: `position_legs` is the single source of truth for legs and expiries (earliest expiry and last trade cutoff are queries, not stored copies), and the protective orders are found in `orders` by position and role. `create_position` takes the legs as a list instead of `legs_data` and `expiration`; the strategy bot is adapted in the same change.

**Lifecycle.** Today `virtual_positions.status` has no database constraint and the code interprets only PENDING, OPEN, CLOSED and CANCELLED. v4 renames PENDING to ENTRY_PENDING (there are no old rows to keep, and the strategy bot only sees the status through `OrderService`) and adds a `CHECK` constraint listing every allowed state. Add states such as EXIT_CANCEL_PENDING, EXIT_WORKING, PARTIALLY_EXPIRED, NEEDS_RECONCILIATION on top, and move the transitions into one guarded function. Today `OrderService._apply_final_status` acts only on PENDING (ENTRY_PENDING in v4) and OPEN; `get_open_positions` returns OPEN only, so positions in the new "still exposed" states must be added to every query that decides exposure or max-open counts. The OPEN transition fires only once the opening order reaches a done state, with `quantity = int(filled)`; a partially filled entry that is still working has no OPEN position and no protection, so add an explicit partial-fill hook (see section 6B, "Protect partial entry fills promptly").

**Fills.** `fills.exec_id` as primary key with `INSERT OR IGNORE` already makes duplicate callbacks idempotent; commissions are attached later by `commissionReportEvent`. Combo executions may arrive as the BAG and/or as individual legs (`fills.con_id`/`sec_type`). Derive position quantity and entry price `E` from the order (`orders.filled_quantity`, `avg_fill_price`) or from one consistent row type, never by summing BAG and leg rows together. Verify which rows IB sends in paper before relying on either. Also check how IB numbers corrected executions: if a correction arrives under a changed `execId` suffix, `INSERT OR IGNORE` would count it twice.

CLOSED requires confirmed close/settlement of all exposure, not merely a date passing. `OrderService` today sets CLOSED as soon as a take-profit, stop-loss or close order reaches Filled, which is correct only when that order covered every leg and the full quantity. For partially filled closes, mixed expiries and expired legs, CLOSED must also check `position_legs`.

On restart/reconnect: reconcile open orders, recent executions, stored fills and aggregate account positions before permitting entries. The framework gives a starting point: `OrderService.attach()` replays `ib.trades()` into the database on every start, and every framework order carries its `orderRef`, so a GTC exit that survived the restart is recognised by `parse_order_ref` rather than re-placed. `attach()` is a no-op on repeated calls, and ib_async does not deliver orders and executions replayed on connect as live events, so call `sync_from_ib` explicitly after every reconnect (for example from `ib.connectedEvent`), not only at startup. Orders placed outside the framework have no framework `orderRef` and are ignored (the Phase 2 manual-close convention in section 7 is the planned, strictly defined exception). They still count in broker positions, so they are a reconciliation discrepancy, never silently adopted. Broker net positions cannot identify individual overlapping strategies; aggregate all virtual legs across bots and flag discrepancies, never classify one bot as missing solely because its legs net against another bot. Preserve valid GTC orders; do not duplicate an order just because the local in-memory trade is missing. Resolve gaps outside available execution history using account statements/Flex or operator review. Daily latches survive all recovery paths, including database export/import: the `entry_latches` rows are insert-only, so a merge keeps the union and a latch is never lost or reset. Importing into a database while the farm runs is unsupported (`db_tool.py` already requires the farm to be stopped); after restoring a database from another machine, run the same reconciliation before permitting entries. Reconciliation is still missing in the framework (there is no `reqPositions` comparison today), so it is a Phase 1 work item rather than an inherited feature.

For deliberate leg expiry, cancel stale combo protection before the leg becomes untradeable, reconcile settlement/assignment, and only rebuild protection on a well-defined remaining position. Do not blindly apply original percentage targets to a changed leg set. Phase 1 retains exposure and requests intervention if residual cost basis/order eligibility cannot be determined safely. This is supported configuration with explicit operational limitations, not a validation error.

### D. Selection, sizing and orchestration

Extend shared option utilities for per-leg expirations, exact/nearest DTE, linked strikes, safe trading-class mapping and contract-specific ticks/multipliers. OO backtests SPX PM expirations; do not accidentally select standard AM-settled SPX contracts. Validate the broker's supported leg count independently of OO's eight-leg schema limit.

An `AccountService` provides account-scoped equity, buying power and what-if margin estimates for Phase 2 sizing. Never represent fixed quantity as ignoring real margin requirements. Add concurrency-safe risk reservations across bots.

Create `bots/optionomega/{__init__.py,config.py,bot.py}` as a thin orchestrator using existing BotBase, shared IBConnectionManager and targeted RequestTracker/ErrorDispatcher conventions. Do not subscribe each bot directly to global IB errors. Implement cancellation-safe startup/shutdown, remove event subscriptions, release data references and preserve acknowledged GTC exits. Persistence is shared: `OrderService` is created once per database and its event subscriptions are not tied to one bot, so a bot's `stop()` must not call `detach` (that would stop persistence for the other bots). On shutdown, use `cancel_unprotected_orders`, which cancels everything except take-profit and stop-loss orders. That includes a working `close` order of a soft-exit chase; if an exit chase had already cancelled the broker protection, shutdown would leave the position without protection. Shutdown therefore needs a protection-restoring step (or must refuse to cancel the chase) for positions in an exit-working state. No bot constructs `PositionManager` directly anymore. Use `self.order_service` and read through `order_service.position_manager`. Farm downtime disables soft exits and deadlines even though GTC orders remain; surface this limitation prominently.

### Existing framework reality

Re-inspected after commit `3fcc5f0`. This replaces the earlier finding that the strategy bot never called the persistence layer.

- Persistence is wired in: `bots/strategy/bot.py` calls `order_service.attach`, `create_position`, `place_order` (open, take-profit, stop-loss), `modify_order` (chase), `cancel_order` and `cancel_unprotected_orders`. Orders, fills, commissions, rejection reasons and the PENDING, OPEN, CLOSED and CANCELLED transitions are persisted from IB events. The unit tests mock IB; nothing is paper-validated yet (BAG execution reports, BAG stop and OCA acceptance, migration of a real live database on first start).
- Still absent: an active exit-evaluation loop (`ExitConditionEvaluator` is constructed but never evaluated), any reconciliation against `reqPositions`, and any caller of `PositionManager.get_open_positions` or `get_position_by_order_ref` outside the class itself. The strategy bot still places the stop-loss and take-profit as separate non-OCA orders.
- `legs_data` is a JSON blob. The strategy bot stores `con_id`, `local_symbol`, `right`, `strike`, `expiration` and `ratio` per leg there. v4 replaces it with `position_legs` and the strategy bot writes its legs there. There is no backfill.
- Existing strategy behavior (what it trades and how) is not changed by this plan, but its code is adapted to the v4 schema in the same change: legs go to `position_legs`, PENDING becomes ENTRY_PENDING, and existing database files are not migrated. Roles and the `orderRef` format stay unchanged. Its tests are updated accordingly and must still pass.
- Memory-bank statements about the older persistence state are outdated where they conflict with this section; the code is authoritative.

## 7. Implementation sequence and acceptance gates

### Phase 1: core runtime, including floating entry

1. Save this specification; implement strict schema, ignored-key report, pure pricing/premium-filter functions, and documentation fixtures.
2. Prove broker combo GTC PT/STP/STP LMT and OCA compatibility in paper, or clearly block unsupported configurations. Do this before enabling entry for configurations requiring hard exits.
3. Redesign the schema cleanly as v4 on top of the existing persistence layer (no second persistence path, no migration of v1-v3 files): `entry_latches`, `position_legs`, `order_events`, typed `orders` columns, reshaped `virtual_positions` and lifecycle states; `OrderService` hooks for partial fills, rejected protection, awaitable cancel confirmation and reconnect resync; `db_transfer` support for each new table. Add fresh-schema, old-version-rejection, export/import and recovery tests, and adapt the strategy bot and its tests to v4.
4. Build shared market data, per-leg selection and session-aware scheduler (fixed plus floating).
5. Implement bounded limit chaser, protective broker orders and race-safe exit coordination.
6. Add monitoring for explicit soft rules, first-expiry deadlines, and safe partial-expiry handling; wire orchestrator and reconciliation.
7. Only then add a disabled/paper deployment example. Never enable a live strategy or submit orders as part of offline tests.

Phase 1 includes fixed-quantity sizing, per-leg DTE, delta/linked-offset selection, filters, floating entry, broker hard exits, managed risk/time exits, and restart safety. Acceptance requires tests for:

- Credit/debit PT/SL in each unit; tick rounding and SELL-close adapter signs.
- All four bounds, missing pairs, OO side-agnostic filter on the anchor quote and on every limit, mutually exclusive families, zero/sign-crossing prices, no quote clamping, chase bounds and favorable fills.
- Slippage is measured, not configured: the reference price is stored per order; entry, stop, profit-target and soft-exit slippage are derived with the correct adverse sign. A soft-exit chase starts at mid, approaches the bid (selling) or ask (buying) in increments, never goes beyond it and never becomes a market order.
- The runtime YAML rejects `backtest_only` and any slippage key as unknown keys.
- Hard-stop default only when a stop exists; explicit no-exit means no exit; false first-expiry policy accepted for calendars.
- Floating latch consumed before selection; no retry after rejection/no-fill/error; persistence across restart and New York date/DST rollover.
- Stale data, missing Greeks, disconnected feeds and subscription sharing.
- Partial entry/exit fills, OCA reduction, cancel/fill races, duplicate executions, rejected protection and failed chase protection recovery.
- Early closes/holidays and correct expiry cutoff; failed deadline close; deliberate first-leg expiry with remaining legs tracked; assignment requiring intervention.
- Offline bracket fills, orphaned/missing orders, shared-leg netting across bots and the strategy bot's behavior on the v4 schema.
- Fresh v4 schema creation with all constraints; a database with an older `user_version` is rejected with a clear message and not modified; the strategy bot's orders and positions still behave as before on v4.
- Export/import round trip including each new table: the entry latch union survives a merge, a repeated import changes nothing, an old-format file is rejected clearly, and `entry_latches`, `position_legs` and `order_events` rows keep their natural keys on both machines.
- Config edit (any YAML change) creates a new `bots` version, but the entry latch, max-open count and recovery still apply to the same `bot_id`.
- Replayed fills after a reconnect are not double counted; a closing fill on an already CLOSED position and a combo fill reported as both BAG and leg rows do not change quantity or entry price twice.

Use the existing standard-library `unittest` patterns and mocked IB (`tests/test_order_service.py` uses `unittest.TestCase` with a `MagicMock` IB and a temporary SQLite file; add `IsolatedAsyncioTestCase` for the async chaser and scheduler); no new test library is needed. Run by module name, for example `python -m unittest tests.test_db_transfer tests.test_order_service`, because `tests` has no `__init__.py` and `unittest discover` does not work. Paper tests are separate, explicitly authorized broker operations, not part of automated discovery tests.

### Phase 2

Margin/capital sizing; additional indicators and entry selectors; short/long ratios; trailing behavior; exit liquidity controls; prune-oldest; supported re-entry modes; higher-frequency monitoring with capacity checks; manual close of framework-opened positions (below). Each needs explicit schema/execution semantics and tests.

**Manual close of framework-opened positions (to be defined).** A human closes a position by placing an order in TWS whose `orderRef` follows a convention, for example `Close <VPID>`, where VPID is the Virtual Position ID (`virtual_positions.position_id`). Today such an order is ignored like any order without a framework `orderRef`. Open design points:

- Whether the farm adopts the human's order as the position's `close` order, or treats the reference only as a signal and runs its own limit chaser close.
- How competing protective GTC orders are cancelled first, and how a race with a protective fill is detected (overfill leads to NEEDS_RECONCILIATION).
- Strict parsing and validation: known VPID, position still exposed, order legs, side and quantity match the remaining position; otherwise reject and alert.
- A human-friendly VPID form: the full UUID hex is long to type; for example a short unique prefix or a per-bot sequence number shown in reporting.
- Audit: record the event as a manual close with its source; decide whether the order is stored under the framework `orderRef` scheme or the human reference, which affects `parse_order_ref` and the `orders` natural key in `db_transfer`.

### Phase 3

Leg groups, spread-premium targeting, per-leg stop behavior, profit/time actions, OptionOmega import/mapping tooling (not the existing `db_tool.py` database export/import) and a complete supported-settings reference.

## 8. Delivery status

This document records the agreed plan. It was revised on 2026-11-02 after commit `3fcc5f0` (OrderService, schema v3, export/import) to build on that persistence layer instead of proposing a parallel one, and again on 2026-11-03: live-only YAML (no `backtest_only`), slippage measured instead of configured with soft exits on a limit chaser, a clean schema v4 without backward-compatibility constraints, a Phase 2 manual-close feature (to be defined) and no external signal providers. No new bot, schema change, framework service, demo configuration or trading order is implemented or enabled by this document. Next implementation milestone is Phase 1 step 1, with broker capability validation as a release gate. Phase 1 step 3 is smaller than originally planned (order, fill, commission and version persistence already exist) but now carries two obligations: adapt the strategy bot and its tests to the clean v4 schema without changing its trading behavior, and keep `db_transfer` complete for every new table.
