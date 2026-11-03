# Strategy Bot (`strategy`)

## 1. Overview

The `strategy` bot is a production options strategy execution engine designed to trade multi-leg option spreads (e.g., Butterflies, Iron Condors, Bull Put Spreads, Jade Lizards, Ratio Spreads) on scheduled market days and times. It operates on indices (such as SPX, XSP, NDX, RUT) or single stocks (such as AAPL, TSLA).

### Key Features
- **Flexible Leg Construction**: Define complex multi-leg combinations with any number of buy/sell legs and contract ratios.
- **Intelligent Strike Selection**:
  - `delta`: Greeks-based strike selection using `find_option_by_delta` (with fallback searches for illiquid strikes).
  - `underlying_offset`: Strike offset relative to the spot price of the underlying asset.
  - `leg_offset`: Strike offset relative to the strike of another defined leg (parent/child hierarchy).
- **Market Entry Conditions**: Pre-trade filters that evaluate market metrics (Simple Moving Average, intraday percentage move from open, VIX volatility level) before placing orders.
- **Mid-Price Sampling & Smart Limit Execution**:
  - Samples combo mid-prices across all legs over a configurable window (`mid_price_monitoring_period`) to determine a fair execution price.
  - Places limit combo orders (`BAG`) and gradually steps the price by minimum tick increments (`max_price_adjustments`) towards fills while enforcing strict premium bounds (`min_premium`, `max_premium`).
- **GTC Bracket Order Placement**:
  - Automatically submits Good-Till-Canceled (GTC) bracket orders (Take Profit limit orders and Stop Loss stop orders) linked by One-Cancels-All (OCA) groups upon entry execution.
- **Virtual Position Persistence**:
  - Records fills, legs, Greeks, and bracket order IDs into the framework's SQLite database (`virtual_positions`), isolating strategy instances from broker portfolio netting.
- **Safe Lifecycle & Preserved Stops**:
  - On bot shutdown or system restart, in-flight entry limit orders being adjusted are safely cancelled, while protective GTC Stop Loss and Take Profit orders remain active on the broker's book.

---

## 2. Configuration Parameters

The Strategy bot configuration file is stored in an environment directory (e.g., `config/live/strategy_fkk.yaml`, `config/demo/strategy_30DTE_butterfly.yaml`). The schema is strictly validated by Pydantic (`extra: "forbid"`).

### Root Configuration Parameters

| Parameter | Type | Required / Default | Constraints / Allowed Values | Description |
| :--- | :--- | :--- | :--- | :--- |
| `type` | `string` | **Required** | Must be `"strategy"` | Bot type identifier. |
| `test_mode` | `boolean` | Optional (`false`) | `true`, `false` | When `true`, evaluates entry conditions and resolves contracts/strikes, but does not place any orders to IBKR. |
| `underlying_type` | `string` | **Required** | `"Index"`, `"Stock"` | Asset class of the underlying. |
| `underlying_symbol` | `string` | **Required** | Non-empty string (e.g., `"SPX"`, `"XSP"`, `"AAPL"`) | Ticker symbol of the underlying asset. |
| `underlying_exchange` | `string` | Optional (`"SMART"`) | Valid exchange (e.g., `"CBOE"`, `"SMART"`) | Exchange routing destination for the underlying asset. |
| `dte` | `integer` | **Required** | `0` to `365` | Target days to expiration for the option legs. |
| `use_exact_dte` | `boolean` | Optional (`false`) | `true`, `false` | If `true`, only selects contracts expiring on the exact DTE. If `false`, searches for the closest available expiration (e.g. +/- 1, 2, 3 days). |
| `entry_days` | `list[string]` | **Required** | Minimum 1 item (`"Monday"`, `"Tuesday"`, `"Wednesday"`, `"Thursday"`, `"Friday"`, `"Saturday"`, `"Sunday"`) | Days of the week when the bot evaluates and executes trades. |
| `entry_times` | `list[string]` | **Required** | Minimum 1 item, format `"HH:MM"` (24-hour clock) | Scheduled times when trade evaluation triggers. |
| `timezone` | `string` | Optional (`"America/New_York"`) | Valid IANA timezone name (e.g., `"America/New_York"`, `"Europe/Berlin"`) | Timezone used to evaluate `entry_days` and `entry_times`. |
| `num_contracts` | `integer` | Optional (`1`) | `1` to `100` | Number of strategy spread units to trade. |
| `legs` | `list[object]` | **Required** | Minimum 2 items | Spread leg definitions. See [Leg Configuration](#leg-configuration-legs) below. |
| `entry_conditions` | `list[object]` | Optional (`null`) | List of condition objects | Optional filters that must all evaluate to true before entering a trade. See [Entry Conditions](#entry-conditions-entry_conditions). |
| `mid_price_monitoring_period` | `integer` | Optional (`10`) | `1` to `300` seconds | Duration in seconds to sample mid-prices across all legs to calculate initial limit order price. |
| `min_premium` | `float` | Optional (`null`) | Any float (signed: negative for credit, positive for debit) | Lower bound on acceptable combo execution price. Must be `<= max_premium`. |
| `max_premium` | `float` | Optional (`null`) | Any float (signed: negative for credit, positive for debit) | Upper bound on acceptable combo execution price. Must be `>= min_premium`. |
| `max_price_adjustments` | `integer` | Optional (`4`) | `0` to `20` | Maximum number of incremental price steps towards the market if the order is not filled. |
| `price_adjustment_wait_seconds` | `integer` | Optional (`30`) | `1` to `300` seconds | Wait duration between consecutive price adjustment attempts. |
| `stoploss_factor` | `float` | Optional (`null`) | `>= 0.0` | Multiplier of entry premium used to calculate the Stop Loss trigger price for the GTC bracket order. |
| `takeprofit_factor` | `float` | Optional (`null`) | `>= 0.0` | Multiplier of entry premium used to calculate the Take Profit limit price for the GTC bracket order. |
| `exit_conditions` | `list[object]` | Optional (`null`) | List of exit condition objects | Early exit condition rules. See [Exit Conditions](#exit-conditions-exit_conditions). |


---

### Leg Configuration (`legs[]`)

Each spread is defined by an array of legs (at least 2 legs). Each leg has the following parameters:

| Parameter | Type | Required / Default | Constraints / Allowed Values | Description |
| :--- | :--- | :--- | :--- | :--- |
| `name` | `string` | **Required** | Unique string identifier | Identifier used in logging, order tags, and parent references. |
| `right` | `string` | **Required** | `"call"`, `"put"` | Option type. |
| `ratio` | `integer` | **Required** | Non-zero integer (positive = buy, negative = sell) | Contract quantity ratio for this leg per combo unit (e.g., `-1` to sell 1 contract, `2` to buy 2 contracts). |
| `strike_selection` | `string` | **Required** | `"delta"`, `"underlying_offset"`, `"leg_offset"` | Method for determining the option strike. |
| `strike_selection_delta` | `float` | Conditional (`null`) | `0.0` to `1.0` | Target delta when `strike_selection: "delta"`. Required for `"delta"`, forbidden otherwise. |
| `strike_offset` | `float` | Conditional (`null`) | Any float (points relative to reference) | Strike offset in points. Required for `"underlying_offset"` and `"leg_offset"`, forbidden for `"delta"`. |
| `strike_selection_parent` | `string` | Conditional (`null`) | Name of another leg in `legs` | Required when `strike_selection: "leg_offset"`. Must reference an existing leg name and cannot reference itself. |

---

### Entry Conditions (`entry_conditions[]`)

All configured entry conditions must pass (`AND` logic) for the trade to trigger.

| Parameter | Type | Required / Default | Constraints / Allowed Values | Description |
| :--- | :--- | :--- | :--- | :--- |
| `name` | `string` | **Required** | Non-empty string | Informative label for logging and diagnostic output. |
| `type` | `string` | **Required** | `"SMA"`, `"underlying_intraday_move"`, `"VIX"` | Indicator type. |
| `period` | `integer` | Conditional (`null`) | `>= 1` | Lookback period in days. Required when `type: "SMA"`. |
| `operator` | `string` | **Required** | `">"`, `">="`, `"<"`, `"<="`, `"=="` | Comparison operator. |
| `threshold` | `float` | Conditional (`null`) | Any float | Metric threshold. Required for `"underlying_intraday_move"` (e.g., `0.003` for +0.3%) and `"VIX"` (e.g., `18.5` for VIX level). |

---

### Exit Conditions (`exit_conditions[]`)

Optional conditions monitored to trigger an early position close:

| Parameter | Type | Required / Default | Constraints / Allowed Values | Description |
| :--- | :--- | :--- | :--- | :--- |
| `name` | `string` | **Required** | Non-empty string | Informative label for logging. |
| `type` | `string` | **Required** | `"position_delta"` | Exit evaluation metric. |
| `operator` | `string` | **Required** | `">"`, `">="`, `"<"`, `"<="` | Comparison operator. |
| `threshold` | `float` | **Required** | Any float | Delta threshold that triggers early exit. |


---

## 3. Configuration Examples

### Example 1: FKK-Style Bull Put Spread (`strategy_fkk.yaml`)
```yaml
type: strategy

underlying_type: Index
underlying_symbol: SPX
underlying_exchange: CBOE

dte: 0
use_exact_dte: true

entry_days:
  - Tuesday
  - Wednesday
  - Thursday
  - Friday

entry_times:
  - "13:13"
  - "14:13"

timezone: America/New_York

# Entry conditions
entry_conditions:
  - name: moving_average
    type: SMA
    period: 5
    operator: ">="
  - name: intraday_move
    type: underlying_intraday_move
    operator: ">="
    threshold: 0.003

# Spread legs: Sell 35-delta Put, Buy Put 10 points lower
legs:
  - name: short_put
    right: put
    ratio: -1
    strike_selection: delta
    strike_selection_delta: 0.35
  
  - name: long_put
    right: put
    ratio: 1
    strike_selection: leg_offset
    strike_selection_parent: short_put
    strike_offset: -10

num_contracts: 1

# Pricing: Minimum net credit of $1.30 (represented as negative premium)
mid_price_monitoring_period: 10
max_premium: -1.30
max_price_adjustments: 2
price_adjustment_wait_seconds: 30

# Bracket orders (null = no bracket)
stoploss_factor: null
takeprofit_factor: null
```

### Example 2: 30 DTE SPX Butterfly Spread
```yaml
type: strategy

underlying_type: Index
underlying_symbol: SPX
underlying_exchange: CBOE

dte: 30
use_exact_dte: false

entry_days:
  - Wednesday

entry_times:
  - "10:00"

timezone: America/New_York

# Classic 1/-2/1 Call Butterfly centered at ATM
legs:
  - name: body
    right: call
    ratio: -2
    strike_selection: underlying_offset
    strike_offset: 0

  - name: lower_wing
    right: call
    ratio: 1
    strike_selection: leg_offset
    strike_selection_parent: body
    strike_offset: -50

  - name: upper_wing
    right: call
    ratio: 1
    strike_selection: leg_offset
    strike_selection_parent: body
    strike_offset: 50

num_contracts: 1
mid_price_monitoring_period: 15
min_premium: 1.00
max_premium: 3.50
max_price_adjustments: 4
price_adjustment_wait_seconds: 20

stoploss_factor: 2.0
takeprofit_factor: 0.5
```

---

## 4. Execution Logic & Pricing Conventions

1. **Signed Premium Notation**:
   - In IBKR and the framework, net debit orders have positive values (`+`), while net credit orders have negative values (`-`).
   - For a net credit spread requiring at least $1.30 credit, set `max_premium: -1.30` (because `-1.40` is more credit and is `<= -1.30`).
2. **Dynamic Price Adjustments**:
   - The initial limit order is placed at the sampled mean mid-price.
   - If not filled within `price_adjustment_wait_seconds`, the limit price steps by `min_tick` towards execution, up to `max_price_adjustments` times, never exceeding `max_premium` or dropping below `min_premium`.
3. **Bracket Order Lifecycles**:
   - Upon fill, Take Profit and Stop Loss orders are placed with `_takeprofit` and `_stoploss` order references as an OCA group.
   - When the bot stops, `stop()` cancels any working entry orders while leaving filled bracket orders active on the broker's book.
4. **Database Inspection**:
   - Filled positions and bracket orders are stored in `data/{env}_positions.db`. Inspect open/closed virtual positions using:
     ```powershell
     python show_db.py data/live_positions.db
     ```

