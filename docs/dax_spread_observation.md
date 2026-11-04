# DAX Spread Observation Bot (`dax_spread_observation`)

## 1. Overview

The `dax_spread_observation` bot is a dedicated risk monitoring and alerting engine designed for manually opened, unmanaged portfolio positions on DAX Index Options (Eurex). It monitors broker-held positions directly from Interactive Brokers without requiring them to be tracked as virtual database positions.

The bot continuously identifies option spreads, checks short leg Greek delta drift, detects when market prices test or breach strike boundaries, audits broker-held Take Profit orders, and delivers scheduled portfolio summaries via Telegram.

### Key Features
- **Automated Spread Pairing**:
  - Automatically identifies **Bear Call Spreads** (Credit Call Spreads) and **Bull Put Spreads** (Credit Put Spreads) from raw broker portfolio items (`ib.portfolio()`).
  - Matches short and long legs by expiration and contract right, supporting varying quantities and multi-spread rolling scenarios for the same expiration.
- **Short Leg Delta Growth Alert**:
  - Subscribes to live or delayed Greeks through the framework `MarketDataService`.
  - Dispatches immediate Telegram alerts when the short leg delta exceeds a configured risk threshold (e.g. $|\Delta| > 0.38$).
- **Strike Testing & Breach Alerts**:
  - Continuously calculates underlying distance to the short strike in index points and percentages.
  - Sends a **Short Strike Tested** alert when the underlying DAX price enters the safety buffer (e.g. within 50 points of the short strike).
  - Sends a critical **Long Strike Tested** alert if the underlying penetrates the protective long strike (maximum loss zone).
- **Take-Profit Order Auditing**:
  - Inspects active broker working orders (`ib.openTrades()`) to verify whether protective GTC limit closing orders exist for each spread.
- **Scheduled Daily Portfolio Broadcast**:
  - Delivers formatted HTML summary tables to Telegram at configured times (e.g. 09:15 and 17:45 CET) showing positions, strikes, unrealized P&L, delta, buffers, and take-profit status.
- **Alert Debouncing & Cooldown**:
  - Enforces per-spread alert cooldowns (e.g. 60 minutes) to eliminate repetitive tick-by-tick notification noise while conditions persist.

---

## 2. Configuration Parameters

The configuration file is stored in an environment directory (e.g., `config/live-read/dax_spread_monitor.yaml`, `config/live/dax_spread_monitor.yaml`). The schema is strictly validated by Pydantic (`extra: "forbid"`).

### Root Configuration Parameters

| Parameter | Type | Required / Default | Constraints / Allowed Values | Description |
| :--- | :--- | :--- | :--- | :--- |
| `type` | `string` | **Required** | Must be `"dax_spread_observation"` | Bot type identifier. |
| `name` | `string` | Optional (`"DAX Spread Observer"`) | Any string | Human-readable name for the bot instance in logs. |
| `connection` | `string` | Optional (`"default"`) | Non-empty string | Logical name of Telegram credentials in `.secret-config.yaml`. |
| `account` | `string` | Optional (`null`) | Valid IB account ID (e.g. `"U10702503"`) | Optional account filter. Defaults to `connection.selected_account`. |
| `symbol` | `string` | Optional (`"DAX"`) | Non-empty string | Underlying index symbol to filter portfolio positions. |
| `exchange` | `string` | Optional (`"EUREX"`) | Exchange name | Exchange destination for qualifying the underlying index and options. |
| `currency` | `string` | Optional (`"EUR"`) | ISO 3-letter currency code | Denominated currency. |
| `market_data_type` | `integer` | Optional (`3`) | `1` (Live), `2` (Frozen), `3` (Delayed), `4` (Delayed Frozen) | IB market data subscription type. Use `3` if Eurex API feed is delayed. |
| `max_short_delta` | `float` | Optional (`0.38`) | `0.0` to `1.0` | Maximum allowable absolute delta on short option legs before triggering an alert. |
| `strike_tested_buffer_points` | `float` | Optional (`50.0`) | `>= 0.0` | Index point distance between market price and short strike to trigger a tested alert. |
| `monitoring_interval_seconds` | `float` | Optional (`60.0`) | `>= 1.0` | Frequency in seconds at which portfolio positions and market data are scanned. |
| `daily_summary_schedule` | `list[string]` | Optional (`["09:15", "17:45"]`) | List of `"HH:MM"` (24-hour) strings | Daily times to send portfolio summary broadcasts. |
| `timezone` | `string` | Optional (`"Europe/Berlin"`) | Valid IANA timezone name | Timezone in which daily summary schedule and timestamps are evaluated. |
| `alert_cooldown_minutes` | `float` | Optional (`60.0`) | `>= 0.0` | Minimum minutes between repeat alerts for the same position and condition. |

---

## 3. Configuration Example

### DAX Spreads Monitor (`config/live-read/dax_spread_monitor.yaml`)

```yaml
# DAX Spread Observation Bot Configuration
type: dax_spread_observation
name: "DAX Manual Spreads Monitor"
connection: default
account: "U10702503"

symbol: DAX
exchange: EUREX
currency: EUR
market_data_type: 3 # 3 = Delayed, 1 = Live

# Risk thresholds
max_short_delta: 0.38
strike_tested_buffer_points: 50.0 # Alert when DAX is within 50 pts of short strike

# Monitoring cadence & daily summary schedule
monitoring_interval_seconds: 60.0
daily_summary_schedule:
  - "09:15"
  - "17:45"
timezone: "Europe/Berlin"
alert_cooldown_minutes: 60.0
```

---

## 4. Alert Output Samples

### ⚠️ Short Delta Growth Alert
Sent immediately when a short leg's delta exceeds `max_short_delta`:

```text
⚠️ DAX Short Delta Alert
Strategy: Bear Call Spread (4x)
Expiry: 20261106
Short Strike: 26300C
Long Strike: 26550C (Width: 250)

Short Delta: 0.395 (Threshold: 0.380)
DAX Index: 26,180.50
Strike Buffer: +119.5 pts (+0.46%)
Time: 2026-11-04 14:15:22 CET
```

### ⚡ Short Strike Tested Alert
Sent when the underlying DAX price enters the safety buffer:

```text
⚡ DAX Strike TESTED Alert
Leg: SHORT STRIKE
Strategy: Bear Call Spread (4x)
Expiry: 20261106
Strike: 26300C

DAX Index: 26,275.00
Distance: 25.0 pts away
P&L: -450.00 EUR
Time: 2026-11-04 14:32:05 CET
```

### 🚨 Long Strike Tested Alert (Max Loss Zone)
Sent with critical priority if the market price penetrates the protective long strike:

```text
🚨 DAX Strike BREACHED Alert
Leg: LONG STRIKE (Max Loss Zone)
Strategy: Bear Call Spread (4x)
Expiry: 20261106
Strike: 26550C

DAX Index: 26,562.00
Distance: 12.0 pts beyond
P&L: -1,250.00 EUR
Time: 2026-11-04 15:02:11 CET
```

### 📊 Scheduled Daily Summary
Broadcast at configured daily schedule times (e.g. 09:15 and 17:45 CET):

```text
📊 DAX Option Spreads Daily Summary (U10702503)
Positions: 7 open spreads | DAX: 26,259.44
Total Unrealized P&L: +1,004.84 EUR
Time: 2026-11-04 17:45:00 CET

1. 20261106 Put Spread (5x)
   • Strikes: 24150P / 23900P
   • Buffer: +2109 pts (+8.0%) | Δ: 0.004
   • P&L: +302.92 EUR
   • Protection: ❌ No TP Order

2. 20261113 Call Spread (5x)
   • Strikes: 27000C / 27250C
   • Buffer: +740 pts (+2.8%) | Δ: 0.182
   • P&L: -181.96 EUR
   • Protection: ✅ TP: SELL 5x @ -3.00 (LMT GTC)

3. 20261113 Put Spread (6x)
   • Strikes: 23550P / 23300P
   • Buffer: +2710 pts (+10.3%) | Δ: 0.015
   • P&L: +367.47 EUR
   • Protection: ❌ No TP Order

📋 Open DAX Orders (3):
  • SELL 5x DAX Combo 27000C / 27250C (20261113) @ -3.00 (LMT GTC) [Submitted]
  • SELL 6x DAX Combo 23500P / 23750P (20261127) @ -3.00 (LMT GTC) [Submitted]
  • SELL 5x DAX Combo 27000C / 27250C (20261120) @ -3.00 (LMT GTC) [Submitted]
```

---

## 5. Operational Behavior & Pairing Rules

### 1. Credit Spread Invariants
The bot enforces deterministic credit spread pairing logic:
* **Bear Call Spread**:
  - Short Call quantity $< 0$ at lower strike $K_{short}$.
  - Long Call quantity $> 0$ at higher strike $K_{long} > K_{short}$.
  - Quantities matched as $Q = \min(|Q_{short}|, Q_{long})$.
* **Bull Put Spread**:
  - Short Put quantity $< 0$ at higher strike $K_{short}$.
  - Long Put quantity $> 0$ at lower strike $K_{long} < K_{short}$.
  - Quantities matched as $Q = \min(|Q_{short}|, Q_{long})$.

### 2. Multi-Spread and Rolling Scenarios
If rolling operations or layered entries result in multiple spreads for the same expiration:
- Options are grouped strictly by `(expiration, right)`.
- Calls are sorted ascending by strike, matching the lowest short call with the lowest long call above it.
- Puts are sorted descending by strike, matching the highest short put with the highest long put below it.
- Residual quantities are preserved and matched against subsequent legs.

### 3. Strike Buffer Calculations
Buffer distance represents the safety margin remaining before the option moves in-the-money:
* **Call Spreads**:
  $$\text{Buffer}_{\text{short}} = K_{short} - S_{\text{underlying}}$$
* **Put Spreads**:
  $$\text{Buffer}_{\text{short}} = S_{\text{underlying}} - K_{short}$$
A positive buffer indicates the option is out-of-the-money. A negative buffer indicates penetration.

### 4. Market Data Modes
Under Eurex API rules, option and index market data often requires dedicated Level 1/2 subscriptions or operates in delayed mode (IB error 354/10089). Setting `market_data_type: 3` (Delayed) instructs `ib_async` to request delayed streaming data with full `modelGreeks` computations, ensuring the bot remains fully operational even without professional real-time exchange subscriptions.
