# OptionOmega Bot (`optionomega`)

## 1. Overview

The `optionomega` bot is a production options trading bot modeled after OptionOmega backtest mechanics with live execution adaptations. It supports complex multi-leg options structures—such as calendars, diagonals, condors, butterflies, verticals, and custom ratio spreads—on index and equity underlyings.

### Key Features
- **Strict Pydantic Validation**: Uses `extra: "forbid"` across all models. Rejects backtest-only properties or artificial slippage configurations; live execution measures slippage in real-time.
- **Exchange-Aware Scheduling & Floating Entry Windows**:
  - Supports daily, weekly, monthly, or specific-date schedules.
  - Floating entry windows (`floating_entry`) evaluate entry conditions continuously between `min_time` and `max_time`, consuming an atomic daily entry latch (`entry_latches` table) to guarantee exactly one entry per trading day.
- **Quote Quality Filtering**:
  - Rejects entry or exit pricing if leg bid/ask spreads exceed `max_leg_bid_ask_width` or if market data quotes are older than `max_quote_age_seconds`.
- **Bounded Limit Chaser**:
  - Samples combo mid-prices (`mid_sampling_seconds`) and steps the limit price by `price_increment` every `wait_seconds` up to `max_attempts`.
  - Enforces a hard budget ceiling (`max_adverse_price_change`) protecting against adverse price drift.
- **Canonical Pricing & Targets**:
  - Evaluates Profit Target and Stop Loss using signed profit formulas (`M - E`) supporting `percent`, `fixed_premium`, and `closing_premium` modes.
- **Broker GTC Bracket Protection**:
  - Submits broker GTC Take Profit (Limit) and Stop Loss (Stop or Stop-Limit) orders bound within a broker One-Cancels-All (OCA) group.
- **Expiration Management**:
  - Automated early exit monitoring closes the full multi-leg position `close_full_position_before_first_leg_expires_minutes` before the earliest expiring leg to eliminate assignment risk.
- **Database Schema v4 Persistence**:
  - Stores complete execution state across SQLite tables (`virtual_positions`, `position_legs`, `orders`, `fills`, `entry_latches`, `order_events`).

---

## 2. Configuration Parameters

The configuration file is stored in an environment directory (e.g. `config/live/strategy_calendar.yaml`, `config/demo/optionomega_bot.yaml`). The schema is strictly validated by Pydantic (`extra: "forbid"`). Any unrecognized parameter will fail startup validation.

### Root Configuration Parameters

| Parameter | Type | Required / Default | Constraints / Allowed Values | Description |
| :--- | :--- | :--- | :--- | :--- |
| `type` | `string` | **Required** | Must be `"optionomega"` | Bot type identifier. |
| `name` | `string` | **Required** | Non-empty string | Human-readable strategy name. |
| `test_mode` | `boolean` | Optional (`false`) | `true`, `false` | When `true`, evaluates market data and logs actions without submitting orders to IBKR. |
| `underlying` | `object` | **Required** | Valid `UnderlyingConfig` | Contract definition of the underlying asset. See [Underlying Configuration](#underlying-configuration-underlying). |
| `legs` | `list[object]` | **Required** | Minimum 1 item | List of option leg specifications. See [Leg Configuration](#leg-configuration-legs). |
| `use_exact_dte` | `boolean` | Optional (`false`) | `true`, `false` | If `true`, requires exact DTE match; if `false`, selects nearest available expiration. |
| `round_strikes_to_nearest` | `float` | Optional (`null`) | `> 0` | Optional strike rounding increment (e.g. `5.0`). |
| `allocation` | `object` | Optional (default sub-model) | Valid `AllocationConfig` | Sizing and position concurrency limits. See [Allocation Configuration](#allocation-configuration-allocation). |
| `entry` | `object` | **Required** | Valid `EntryConfig` | Schedule, entry conditions, filters, and execution chaser. See [Entry Configuration](#entry-configuration-entry). |
| `profit_target` | `object` | Optional (`null`) | Valid `ProfitTargetConfig` | Take-profit bracket configuration. See [Profit Target](#profit-target-profit_target). |
| `stop_loss` | `object` | Optional (`null`) | Valid `StopLossConfig` | Stop-loss bracket configuration. See [Stop Loss](#stop-loss-stop_loss). |
| `exit` | `object` | **Required** | Valid `ExitConfig` | Position expiration handling and exit execution. See [Exit Configuration](#exit-configuration-exit). |


---

### Underlying Configuration (`underlying`)

| Parameter | Type | Required / Default | Constraints / Allowed Values | Description |
| :--- | :--- | :--- | :--- | :--- |
| `ticker` | `string` | **Required** | Non-empty string (e.g., `"SPY"`, `"SPX"`) | Ticker symbol of underlying asset. |
| `sec_type` | `string` | Optional (`"IND"`) | `"IND"`, `"STK"` | Security type: `"IND"` for indices, `"STK"` for single stocks/ETFs. |
| `exchange` | `string` | Optional (`"SMART"`) | Valid exchange (e.g., `"SMART"`, `"CBOE"`) | Exchange routing destination. |
| `currency` | `string` | Optional (`"USD"`) | ISO 3-letter currency code | Denominated currency. |

---

### Leg Configuration (`legs[]`)

Each leg defines one side of the option combination:

| Parameter | Type | Required / Default | Constraints / Allowed Values | Description |
| :--- | :--- | :--- | :--- | :--- |
| `id` | `string` | **Required** | Unique string among legs | Unique identifier for this leg (referenced by parent strike offsets). |
| `action` | `string` | **Required** | `"buy"`, `"sell"` | Trade action for this leg. |
| `right` | `string` | **Required** | `"call"`, `"put"` | Option right. |
| `qty` | `integer` | Optional (`1`) | `>= 1` | Contract multiplier quantity for this leg. |
| `dte` | `integer` | **Required** | `>= 0` | Target days to expiration. |
| `strike` | `object` | **Required** | Strike specification object | Rule for selecting strike. See [Strike Specifications](#strike-specifications) below. |

---

### Strike Specifications (`legs[].strike`)

One of the following strike selection types must be specified:

#### 1. Delta Strike (`type: "delta"`)
| Parameter | Type | Required / Default | Constraints / Allowed Values | Description |
| :--- | :--- | :--- | :--- | :--- |
| `type` | `string` | **Required** | Must be `"delta"` | Selects strike nearest to target delta. |
| `value` | `float` | **Required** | `-100.0` to `100.0` | Target delta value (e.g. `30` or `0.30`). |

#### 2. Offset Strike (`type: "offset"`)
| Parameter | Type | Required / Default | Constraints / Allowed Values | Description |
| :--- | :--- | :--- | :--- | :--- |
| `type` | `string` | **Required** | Must be `"offset"` | Offsets strike from another parent leg. |
| `parent` | `string` | **Required** | Existing `id` of another leg | Parent leg ID. Must not create cyclical references. |
| `value` | `float` | Optional (`0.0`) | Any float | Offset in points from parent leg strike. |
| `exact` | `boolean` | Optional (`true`) | `true`, `false` | Whether offset must match available strike grid exactly. |

#### 3. Underlying Offset Strike (`type: "underlying_offset"`)
| Parameter | Type | Required / Default | Constraints / Allowed Values | Description |
| :--- | :--- | :--- | :--- | :--- |
| `type` | `string` | **Required** | Must be `"underlying_offset"` | Offsets strike from live underlying spot price. |
| `value` | `float` | **Required** | Any float | Point offset relative to underlying price. |

#### 4. Fixed Strike (`type: "fixed_strike"`)
| Parameter | Type | Required / Default | Constraints / Allowed Values | Description |
| :--- | :--- | :--- | :--- | :--- |
| `type` | `string` | **Required** | Must be `"fixed_strike"` | Explicit constant strike. |
| `value` | `float` | **Required** | `> 0` | Absolute strike price. |

---

### Allocation Configuration (`allocation`)

| Parameter | Type | Required / Default | Constraints / Allowed Values | Description |
| :--- | :--- | :--- | :--- | :--- |
| `mode` | `string` | Optional (`"fixed_quantity"`) | `"fixed_quantity"` | Position sizing mode. |
| `quantity` | `integer` | Optional (`1`) | `>= 1` | Number of combo units per trade. |
| `max_open_positions` | `integer` | Optional (`1`) | `>= 1` | Maximum concurrent open positions permitted for this bot. |


---

### Entry Configuration (`entry`)

The `entry` block defines timing, market conditions, quote filters, and execution chaser rules:

| Parameter | Type | Required / Default | Constraints / Allowed Values | Description |
| :--- | :--- | :--- | :--- | :--- |
| `frequency` | `object` | **Required** | Valid `FrequencyConfig` | Trading day schedule. See [Entry Frequency](#entry-frequency-entryfrequency). |
| `blackout_dates` | `list[string]` | Optional (`[]`) | Format `"YYYY-MM-DD"` | Explicit calendar dates when entries are suppressed. |
| `times` | `list[string]` | Optional (`[]`) | Format `"HH:MM"` | Fixed entry execution times. If `floating_entry` is enabled, maximum 1 time is permitted. |
| `floating_entry` | `object` | Optional (`null`) | Valid `FloatingEntryConfig` | Floating entry evaluation window. See [Floating Entry](#floating-entry-entryfloating_entry). |
| `conditions` | `list[object]` | Optional (`[]`) | List of condition objects | Market indicators evaluated prior to entry. See [Entry Conditions](#entry-conditions-entryconditions). |
| `min_credit_for_open` | `float` | Optional (`null`) | `>= 0` | Explicit credit lower bound. Mutually exclusive with `option_omega_*` bounds. |
| `max_credit_for_open` | `float` | Optional (`null`) | `>= 0` | Explicit credit upper bound. Must be `>= min_credit_for_open`. |
| `min_debit_for_open` | `float` | Optional (`null`) | `>= 0` | Explicit debit lower bound. Mutually exclusive with `option_omega_*` bounds. |
| `max_debit_for_open` | `float` | Optional (`null`) | `>= 0` | Explicit debit upper bound. Must be `>= min_debit_for_open`. |
| `option_omega_min_entry_premium` | `float` | Optional (`null`) | `>= 0` | OptionOmega side-agnostic premium lower bound. Mutually exclusive with explicit credit/debit bounds. |
| `option_omega_max_entry_premium` | `float` | Optional (`null`) | `>= 0` | OptionOmega side-agnostic premium upper bound. Must be `>= option_omega_min_entry_premium`. |
| `quote_filter` | `object` | Optional (`null`) | Valid `QuoteFilterConfig` | Quote freshness and spread width guards. See [Quote Filter](#quote-filter-entryquote_filter). |
| `execution` | `object` | **Required** | Valid `EntryExecutionConfig` | Entry limit order chasing parameters. See [Entry Execution](#entry-execution-entryexecution). |

---

### Entry Sub-Models

#### Entry Frequency (`entry.frequency`)
| Parameter | Type | Required / Default | Constraints / Allowed Values | Description |
| :--- | :--- | :--- | :--- | :--- |
| `type` | `string` | **Required** | `"weekly"`, `"daily"`, `"monthly"`, `"specific_dates"` | Recurrence pattern. |
| `days` | `list[string]` | Optional (`null`) | `["Mon", "Tue", "Wed", "Thu", "Fri"]` | Days of week when `type: "weekly"`. |
| `dates` | `list[string]` | Optional (`null`) | List of `"YYYY-MM-DD"` dates | Explicit dates when `type: "specific_dates"`. |

#### Floating Entry (`entry.floating_entry`)
| Parameter | Type | Required / Default | Constraints / Allowed Values | Description |
| :--- | :--- | :--- | :--- | :--- |
| `enabled` | `boolean` | Optional (`false`) | `true`, `false` | Enables continuous window condition scanning. |
| `min_time` | `string` | Optional (`"10:00"`) | Format `"HH:MM"`, must be `< max_time` | Earliest time window opens. |
| `max_time` | `string` | Optional (`"15:00"`) | Format `"HH:MM"`, must be `> min_time` | Latest time window closes. |

#### Entry Conditions (`entry.conditions[]`)
| Parameter | Type | Required / Default | Constraints / Allowed Values | Description |
| :--- | :--- | :--- | :--- | :--- |
| `type` | `string` | **Required** | `"underlying_intraday_move"`, `"SMA"`, `"VIX"` | Indicator type. |
| `direction` | `string` | Optional (`null`) | `"up"`, `"down"` | Direction for move indicators. |
| `unit` | `string` | Optional (`null`) | `"percent"`, `"points"` | Unit for move thresholds. |
| `threshold` | `float` | Optional (`null`) | Any float | Numeric threshold value. |
| `period` | `integer` | Optional (`null`) | `>= 1` | Lookback period (e.g. for SMA). |
| `timeframe` | `string` | Optional (`null`) | `"daily"`, `"minute"` | Timeframe for calculation. |

#### Quote Filter (`entry.quote_filter`)
| Parameter | Type | Required / Default | Constraints / Allowed Values | Description |
| :--- | :--- | :--- | :--- | :--- |
| `max_quote_age_seconds` | `float` | Optional (`5.0`) | `> 0` | Maximum acceptable age of market data quotes before rejection. |
| `max_leg_bid_ask_width` | `float` | Optional (`0.50`) | `> 0` | Maximum acceptable bid-ask spread on each option leg. |

#### Entry Execution (`entry.execution`)
| Parameter | Type | Required / Default | Constraints / Allowed Values | Description |
| :--- | :--- | :--- | :--- | :--- |
| `order_type` | `string` | Optional (`"limit"`) | Must be `"limit"` | Order type submitted for entry combo. |
| `mid_sampling_seconds` | `float` | Optional (`10.0`) | `>= 0` | Duration to sample combo mid-prices to form limit anchor. |
| `starting_offset` | `float` | Optional (`0.0`) | `>= 0` | Offset applied to anchor mid-price for initial limit. |
| `price_increment` | `float` | Optional (`0.01`) | `> 0` | Step size in dollars per adjustment attempt. |
| `wait_seconds` | `float` | Optional (`20.0`) | `> 0` | Seconds to wait between price adjustments. |
| `max_attempts` | `integer` | Optional (`5`) | `>= 1` | Maximum adjustment attempts before cancellation. |
| `max_adverse_price_change` | `float` | Optional (`0.05`) | `>= 0` | Maximum cumulative adverse price drift allowed from initial anchor. |

---

### Profit Target (`profit_target`)

| Parameter | Type | Required / Default | Constraints / Allowed Values | Description |
| :--- | :--- | :--- | :--- | :--- |
| `type` | `string` | **Required** | `"percent"`, `"fixed_premium"`, `"closing_premium"` | Target formula mode. |
| `value` | `float` | **Required** | `> 0` | Threshold value (e.g. `50.0` for 50% profit target). |

---

### Stop Loss (`stop_loss`)

| Parameter | Type | Required / Default | Constraints / Allowed Values | Description |
| :--- | :--- | :--- | :--- | :--- |
| `type` | `string` | **Required** | `"percent"`, `"fixed_premium"`, `"closing_premium"` | Stop loss formula mode. |
| `value` | `float` | **Required** | `> 0` | Threshold value (e.g. `100.0` for 100% stop loss). |
| `order_type` | `string` | Optional (`"stop"`) | `"stop"`, `"stop_limit"` | Order type for protective stop. |
| `trigger_method` | `string` | Optional (`"default"`) | Valid IB trigger method | IB trigger method (e.g. `"default"`, `"double_bid_ask"`). |
| `stop_limit_offset` | `float` | Conditional (`null`) | Any float | Limit price offset from stop trigger price. **Required** when `order_type: "stop_limit"`. |

---

### Exit Configuration (`exit`)

| Parameter | Type | Required / Default | Constraints / Allowed Values | Description |
| :--- | :--- | :--- | :--- | :--- |
| `close_full_position_before_first_leg_expires` | `boolean` | **Required** | `true`, `false` | If `true`, closes full position prior to earliest leg expiration. |
| `close_full_position_before_first_leg_expires_minutes` | `integer` | Conditional (`null`) | `>= 1` | Minutes prior to first leg expiration to trigger close. Required if close policy is enabled. |
| `managed_exits` | `list[object]` | Optional (`[]`) | List of managed exit rules | Additional condition-based exit triggers. |
| `monitoring` | `object` | Optional (default sub-model) | Valid `ExitMonitoringConfig` | Position valuation polling cadence. See below. |
| `execution` | `object` | Conditional (`null`) | Valid `ExitExecutionConfig` | Exit limit order chaser parameters. Required if close policy is enabled. See below. |

#### Exit Monitoring (`exit.monitoring`)
| Parameter | Type | Required / Default | Constraints / Allowed Values | Description |
| :--- | :--- | :--- | :--- | :--- |
| `interval_seconds` | `float` | Optional (`60.0`) | `> 0` | Seconds between position valuation checks. |
| `align_to_minute` | `boolean` | Optional (`true`) | `true`, `false` | Whether checks sync to minute clock boundaries. |

#### Exit Execution (`exit.execution`)
| Parameter | Type | Required / Default | Constraints / Allowed Values | Description |
| :--- | :--- | :--- | :--- | :--- |
| `price_increment` | `float` | Optional (`0.01`) | `> 0` | Step size in dollars per exit price adjustment. |
| `wait_seconds` | `float` | Optional (`20.0`) | `> 0` | Seconds between exit adjustment attempts. |
| `max_attempts` | `integer` | Optional (`5`) | `>= 1` | Maximum adjustment attempts before holding order. |


---

## 3. Configuration Example

### Illustrative SPY Calendar Spread (`optionomega_calendar.yaml`)
```yaml
type: optionomega
name: "SPY Illustrative Calendar"
test_mode: false

underlying:
  ticker: SPY
  sec_type: STK
  exchange: SMART
  currency: USD

legs:
  - id: front
    action: sell
    right: call
    qty: 1
    dte: 7
    strike:
      type: delta
      value: 30

  - id: back
    action: buy
    right: call
    qty: 1
    dte: 35
    strike:
      type: offset
      parent: front
      value: 0
      exact: true

use_exact_dte: false
round_strikes_to_nearest: null

allocation:
  mode: fixed_quantity
  quantity: 1
  max_open_positions: 1

entry:
  frequency:
    type: weekly
    days: [Mon, Thu, Fri]
  blackout_dates: []
  times: []
  floating_entry:
    enabled: true
    min_time: '10:00'
    max_time: '14:00'
  conditions:
    - type: underlying_intraday_move
      direction: up
      unit: percent
      threshold: 0.5
  min_debit_for_open: 1.00
  max_debit_for_open: 3.00
  quote_filter:
    max_quote_age_seconds: 5.0
    max_leg_bid_ask_width: 0.50
  execution:
    order_type: limit
    mid_sampling_seconds: 10.0
    starting_offset: 0.00
    price_increment: 0.01
    wait_seconds: 20.0
    max_attempts: 5
    max_adverse_price_change: 0.05

profit_target:
  type: percent
  value: 50.0

stop_loss:
  type: percent
  value: 50.0
  order_type: stop
  trigger_method: default

exit:
  close_full_position_before_first_leg_expires: true
  close_full_position_before_first_leg_expires_minutes: 20
  managed_exits: []
  monitoring:
    interval_seconds: 60.0
    align_to_minute: true
  execution:
    price_increment: 0.01
    wait_seconds: 20.0
    max_attempts: 5
```

---

## 4. Operational Behavior & Validation Rules

1. **Mutual Exclusivity of Premium Filters**:
   - Do not combine explicit bounds (`min_credit_for_open`, `max_credit_for_open`, `min_debit_for_open`, `max_debit_for_open`) with OptionOmega bounds (`option_omega_min_entry_premium`, `option_omega_max_entry_premium`). The bot will reject the configuration on startup if both families are defined.
2. **Acyclic Parent References**:
   - Legs using `type: "offset"` must point to an existing parent leg `id`. Self-references and circular offset graphs are caught and rejected during startup validation.
3. **Daily Entry Latching**:
   - Upon meeting entry conditions, the bot atomically records an entry latch into the SQLite `entry_latches` table for the current `(bot_id, trading_date)`. This guarantees no duplicate entries occur on the same trading day even after bot reboots.
4. **Clean Schema v4 Persistence**:
   - The OptionOmega runtime operates exclusively with Database Schema Version 4 (`PRAGMA user_version = 4`), maintaining full relational integrity across:
     - `bots`: Registered bot instances and configuration version hashes.
     - `entry_latches`: Daily entry consumption records.
     - `virtual_positions`: 8-state position lifecycle (`PENDING_ENTRY`, `ACTIVE`, `CLOSING`, `CLOSED`, etc.).
     - `position_legs`: Normalized individual option legs with expiration dates.
     - `orders`: Typed order records with OCA groups and trigger methods.
     - `fills`: Leg and combo fill execution reports with commissions.
     - `order_events`: Full audit trail of order transitions.

