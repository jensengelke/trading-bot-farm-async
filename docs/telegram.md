# Telegram Notification Bot (`telegram`)

## 1. Overview

The `telegram` bot provides asynchronous real-time notifications for automated trading operations, order state transitions, execution fills, and account health across all running bots in the Trading Bot Farm. It runs as an observer bot within the framework's single event loop and connects to the shared Interactive Brokers connection managed by `IBConnectionManager`.

With the framework refactoring, generic messaging infrastructure (`TelegramClient`, `get_telegram_client`), account snapshot services (`framework/account_service.py`), and HTML formatting engines (`framework/formatters.py`) reside at the framework level, allowing the `telegram` bot to focus strictly on overall account lifecycle events while specialized strategy bots (such as `dax_spread_observation`) reuse the same notification channel.

### Key Features
- **Connection Lifecycle Monitoring**: Hooks directly to `ib.connectedEvent` and `ib.disconnectedEvent` to provide instant alerts when IB Gateway/TWS drops or reconnects.
- **Real-Time Order Monitoring**: Listens to IBKR order and execution events (`openOrderEvent`, `orderStatusEvent`, `execDetailsEvent`).
- **Rich HTML Alerts**: Delivers cleanly formatted alerts with status emojis, leg breakdown for multi-leg combo spreads, limit prices, filled prices, execution commissions, order references (`orderRef`), and timestamps.
- **Account & Order Filtering**: Selectively monitors specific IB accounts or filters notifications by `orderRef` prefixes (e.g. ignoring test or verify orders).
- **Daily Account Summary**: Broadcasts scheduled daily snapshots of Net Liquidation Value, Total Cash, Available Funds, Buying Power, Unrealized P&L, open positions count, and working orders count.
- **Startup Readiness Overview**: Dispatches a comprehensive readiness overview on startup showing cash balances, active portfolio positions, and working orders.
- **Two-Tier Secret Isolation**: Sensitive bot tokens and chat IDs remain strictly in `.secret-config.yaml` (git-ignored), referenced by logical connection name.
- **Debounced Delivery**: Suppresses duplicate status transmissions and transient state fluctuations.
- **Async Non-Blocking Transport**: Delivers messages via non-blocking standard library HTTP requests with rate-limit exponential backoff and automatic message splitting for messages exceeding Telegram's 4096-character limit.

---

## 2. Configuration Parameters

The bot configuration file is stored in an environment directory (e.g., `config/live-read/telegram_notifier.yaml`, `config/live/telegram_notifier.yaml`). The schema is strictly validated by Pydantic (`extra: "forbid"`).

### Root Configuration Parameters

| Parameter | Type | Required / Default | Constraints / Allowed Values | Description |
| :--- | :--- | :--- | :--- | :--- |
| `type` | `string` | **Required** | Must be `"telegram"` | Bot type identifier. |
| `name` | `string` | Optional (`null`) | Any string | Optional display name for the bot instance in logs. |
| `connection` | `string` | Optional (`"default"`) | Non-empty string | Logical name of the Telegram credentials entry in `.secret-config.yaml`. |
| `silent_startup` | `boolean` | Optional (`false`) | `true`, `false` | When `true`, suppresses the startup readiness summary sent when the bot connects. |
| `notifications` | `object` | Optional (default sub-model) | Valid `OrderNotificationsConfig` | Settings governing real-time order, fill, and connection alerts. See below. |
| `daily_summary` | `object` | Optional (default sub-model) | Valid `DailySummaryConfig` | Settings governing scheduled daily account balance broadcasts. See below. |
| `observations` | `object` | Optional (default sub-model) | Valid `ObservationsConfig` | Settings governing periodic portfolio scans (Phase 2). |

---

### Order & Connection Notifications (`notifications`)

Governs real-time order lifecycle alerts, fills, and connection status transitions:

| Parameter | Type | Required / Default | Constraints / Allowed Values | Description |
| :--- | :--- | :--- | :--- | :--- |
| `enabled` | `boolean` | Optional (`true`) | `true`, `false` | Master toggle for real-time notifications. |
| `notify_on_connection` | `boolean` | Optional (`true`) | `true`, `false` | Send alert when IB Gateway connection connects or disconnects. |
| `notify_on_placement` | `boolean` | Optional (`true`) | `true`, `false` | Send alert when a new order is submitted to IBKR (`Submitted` status). |
| `notify_on_status_change` | `boolean` | Optional (`true`) | `true`, `false` | Send alert on intermediate state transitions (e.g., `PreSubmitted`). |
| `notify_on_cancellation` | `boolean` | Optional (`true`) | `true`, `false` | Send alert when an order is cancelled (`Cancelled` status). |
| `notify_on_fill` | `boolean` | Optional (`true`) | `true`, `false` | Send alert upon execution fill with fill price and commissions (`Filled` status). |
| `notify_on_price_adjustments` | `boolean` | Optional (`false`) | `true`, `false` | Send alert whenever a limit chaser adjusts an order price (can be noisy during tick chasing). |
| `filter_accounts` | `list[string]` | Optional (`[]`) | List of IBKR account numbers (e.g., `["U10702503"]`) | If non-empty, only notifications for these accounts are forwarded. Empty list monitors all accounts. |
| `include_order_refs` | `list[string]` | Optional (`[]`) | List of string prefixes or patterns | If non-empty, only orders whose `orderRef` contains one of these patterns are forwarded. |
| `exclude_order_refs` | `list[string]` | Optional (`[]`) | List of string prefixes (e.g., `["verify_*"]`) | Orders matching these `orderRef` patterns are ignored. |

---

### Daily Account Summary (`daily_summary`)

Settings for scheduled daily overall account health broadcasts:

| Parameter | Type | Required / Default | Constraints / Allowed Values | Description |
| :--- | :--- | :--- | :--- | :--- |
| `enabled` | `boolean` | Optional (`false`) | `true`, `false` | Master toggle for scheduled daily account summary alerts. |
| `time` | `string` | Optional (`"22:00"`) | Format `"HH:MM"` (24-hour) | Scheduled daily time to send the summary broadcast. |
| `timezone` | `string` | Optional (`"America/New_York"`) | Valid IANA timezone name | Timezone in which `time` is evaluated (e.g., `"Europe/Berlin"`). |

---

### Portfolio Observations (`observations`)

Settings for periodic custom rule evaluations (Phase 2):

| Parameter | Type | Required / Default | Constraints / Allowed Values | Description |
| :--- | :--- | :--- | :--- | :--- |
| `enabled` | `boolean` | Optional (`false`) | `true`, `false` | Master toggle for scheduled portfolio scans. |
| `schedule` | `string` | Optional (`"0 9,15 * * 1-5"`) | Valid 5-part cron expression | Cron schedule for running observation rules. |
| `timezone` | `string` | Optional (`"America/New_York"`) | Valid IANA timezone name | Timezone in which `schedule` is evaluated. |
| `rules` | `list[object]` | Optional (`[]`) | List of rule definitions | List of observation rule objects to evaluate. |

---

## 3. Configuration Setup & Examples

### Step 1: Create Telegram Bot & Obtain Credentials
1. Message **@BotFather** on Telegram.
2. Send `/newbot` and follow prompts to obtain your HTTP API bot token.
3. Obtain your target **chat ID**:
   - For direct messages: message `@userinfobot` or check `https://api.telegram.org/bot<TOKEN>/getUpdates`.
   - For groups/channels: add the bot as administrator, post a test message, and retrieve the negative channel ID (e.g. `-100...`) via `getUpdates`.

### Step 2: Add Credentials to `.secret-config.yaml`
```yaml
telegram:
  connections:
    default:
      token: "123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ"
      chat_id: "-1001234567890"
```

### Step 3: Configure Bot in `telegram_notifier.yaml`
```yaml
type: telegram
name: "Production Order & Connection Notifier"
connection: default
silent_startup: false

notifications:
  enabled: true
  notify_on_connection: true
  notify_on_placement: true
  notify_on_status_change: true
  notify_on_cancellation: true
  notify_on_fill: true
  notify_on_price_adjustments: false
  
  filter_accounts:
    - "U10702503"
  include_order_refs: []
  exclude_order_refs:
    - "verify_*"

daily_summary:
  enabled: true
  time: "22:00"
  timezone: "Europe/Berlin"

observations:
  enabled: false
  schedule: "0 9,15 * * 1-5"
  timezone: "America/New_York"
  rules: []
```

---

## 4. Alert Output Samples

### 🟢 IBKR Connected & Online
Sent immediately upon connection to IB Gateway/TWS:
```text
🟢 IBKR Connected & Online
Account: U10702503
Gateway: 127.0.0.1:4002
Time: 2026-11-04 13:10:01 EST
```

### 🔴 IBKR Disconnected
Sent immediately if connection to IB Gateway/TWS drops:
```text
🔴 IBKR Disconnected!
Account: U10702503
Gateway: 127.0.0.1:4002
Details: Connection to Gateway/TWS dropped
Time: 2026-11-04 13:10:15 EST
```

### 🟢 Telegram Notifier Connected & Ready (Startup Message)
Sent on bot startup (unless `silent_startup: true`):
```text
🟢 Telegram Notifier Connected & Ready
Instance / Bot: telegram_notifier
Account: U10702503
Connection: default
Time: 2026-11-04 13:10:02 EST

💰 Account Balances:
  • Net Liquidation: 105,432.50 USD
  • Total Cash: 54,321.00 USD
  • Available Funds: 80,000.00 USD
  • Buying Power: 160,000.00 USD
  • Unrealized P&L: +1,245.50 USD

📊 Portfolio Positions (2):
  • IBM (Stock): +100 @ 150.25 (Mkt: 152.10, P&L: +$185.00)
  • SPX Combo (3 legs): +1 @ 2.40 (Mkt: 2.80, P&L: +$40.00)

📋 Open Orders (1):
  • BUY 5 AAPL (Stock) (LMT @ 155.00) [Submitted] | Ref: strategy_aapl_001
```

### 🟢 Order Submitted
```text
🟢 Order Submitted
Account: U10702503
Contract: SPX Combo (3 legs)
Action: BUY 1 BAG @ 2.45
Order ID: 1042
Order Ref: strategy_30DTE_butterfly_001
Legs:
  • #1: BUY 1x conId 7248101
  • #2: SELL 2x conId 7248102
  • #3: BUY 1x conId 7248103
Time: 2026-11-04 10:30:15 EST
```

### 🎯 Order Executed (Fill)
```text
🎯 Order Executed
Account: U10702503
Contract: SPX Combo (3 legs)
Fill: BOT 1 @ 2.40
Progress: 1 / 1 (Filled)
Order Ref: strategy_30DTE_butterfly_001
Order ID: 1042
Commission: 4.20 USD
Time: 2026-11-04 10:30:42 EST
```

### 🔴 Order Cancelled
```text
🔴 Order Cancelled
Account: U10702503
Contract: SPX Combo (3 legs)
Action: BUY 1 BAG
Order ID: 1042
Order Ref: strategy_30DTE_butterfly_001
Status: Cancelled
Time: 2026-11-04 10:31:05 EST
```

### 📊 Daily Account Summary
Broadcast at scheduled daily time (e.g. 22:00):
```text
📊 Daily Account Summary
Account: U10702503
Time: 2026-11-04 22:00:00 CET

• Net Liquidation: 105,432.50 USD
• Total Cash: 54,321.00 USD
• Available Funds: 80,000.00 USD
• Buying Power: 160,000.00 USD
• Unrealized P&L: +1,245.50 USD
• Active Positions: 14
• Working Orders: 2
```


---

### Credential Configuration (`.secret-config.yaml`)

Telegram credentials must be configured in `config/{env}/.secret-config.yaml`:

| Key Path | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `telegram.connections.<connection>.token` | `string` | **Required** | Telegram bot token obtained from @BotFather (e.g., `"123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ"`). |
| `telegram.connections.<connection>.chat_id` | `string` | **Required** | Target user ID or channel/group ID (e.g., `"123456789"` or `"-1001234567890"`). |

