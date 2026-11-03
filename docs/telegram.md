# Telegram Notification Bot (`telegram`)

## 1. Overview

The `telegram` bot provides asynchronous real-time notifications for automated trading operations, order state transitions, and execution fills across all running bots in the Trading Bot Farm. It runs as an observer bot within the framework's single event loop and connects to the shared Interactive Brokers connection managed by `IBConnectionManager`.

### Key Features
- **Real-Time Order Monitoring**: Listens directly to IBKR order and execution events (`openOrderEvent`, `orderStatusEvent`, `execDetailsEvent`).
- **Rich HTML Alerts**: Delivers cleanly formatted alerts with status emojis, leg breakdown for multi-leg combo spreads, limit prices, filled prices, execution commissions, order references (`orderRef`), and timestamps.
- **Account & Order Filtering**: Selectively monitors specific IB accounts or filters notifications by `orderRef` prefixes (e.g. ignoring test or verify orders).
- **Two-Tier Secret Isolation**: Sensitive bot tokens and chat IDs remain strictly in `.secret-config.yaml` (git-ignored), referenced by logical connection name.
- **Debounced Delivery**: Suppresses duplicate status transmissions and transient state fluctuations.
- **Async Non-Blocking Transport**: Delivers messages via non-blocking standard library HTTP requests with rate-limit exponential backoff and automatic message splitting for messages exceeding Telegram's 4096-character limit.
- **Scheduled Observations (Phase 2 Ready)**: Architecture configured for periodic portfolio scans (earnings dates, Greek delta drift).

---

## 2. Configuration Parameters

The bot configuration file is stored in an environment directory (e.g., `config/live/telegram_notifier.yaml`, `config/demo/telegram_notifier.yaml`). The schema is strictly validated by Pydantic (`extra: "forbid"`).

### Root Configuration Parameters

| Parameter | Type | Required / Default | Constraints / Allowed Values | Description |
| :--- | :--- | :--- | :--- | :--- |
| `type` | `string` | **Required** | Must be `"telegram"` | Bot type identifier. |
| `name` | `string` | Optional (`null`) | Any string | Optional display name for the bot instance in logs. |
| `connection` | `string` | Optional (`"default"`) | Non-empty string | Logical name of the Telegram credentials entry in `.secret-config.yaml`. |
| `silent_startup` | `boolean` | Optional (`false`) | `true`, `false` | When `true`, suppresses the *"Telegram Notifier Online"* message sent when the framework starts up. |
| `notifications` | `object` | Optional (default sub-model) | Valid `OrderNotificationsConfig` | Settings governing real-time order and fill alerts. See [Order Notifications](#order-notifications-notifications) below. |
| `observations` | `object` | Optional (default sub-model) | Valid `ObservationsConfig` | Settings governing scheduled portfolio scans. See [Portfolio Observations](#portfolio-observations-observations) below. |

---

### Order Notifications (`notifications`)

Governs real-time order lifecycle alerts and filtering:

| Parameter | Type | Required / Default | Constraints / Allowed Values | Description |
| :--- | :--- | :--- | :--- | :--- |
| `enabled` | `boolean` | Optional (`true`) | `true`, `false` | Master toggle for real-time order notifications. |
| `notify_on_placement` | `boolean` | Optional (`true`) | `true`, `false` | Send alert when a new order is submitted to IBKR (`Submitted` status). |
| `notify_on_status_change` | `boolean` | Optional (`true`) | `true`, `false` | Send alert on intermediate state transitions (e.g., `PreSubmitted`). |
| `notify_on_cancellation` | `boolean` | Optional (`true`) | `true`, `false` | Send alert when an order is cancelled (`Cancelled` status). |
| `notify_on_fill` | `boolean` | Optional (`true`) | `true`, `false` | Send alert upon execution fill with fill price and commissions (`Filled` status). |
| `notify_on_price_adjustments` | `boolean` | Optional (`false`) | `true`, `false` | Send alert whenever a limit chaser adjusts an order price (can be noisy during tick chasing). |
| `filter_accounts` | `list[string]` | Optional (`[]`) | List of IBKR account numbers (e.g., `["U1234567"]`) | If non-empty, only notifications for these accounts are forwarded. Empty list monitors all accounts. |
| `include_order_refs` | `list[string]` | Optional (`[]`) | List of string prefixes or patterns | If non-empty, only orders whose `orderRef` starts with one of these prefixes are forwarded. |
| `exclude_order_refs` | `list[string]` | Optional (`[]`) | List of string prefixes (e.g., `["verify_*"]`) | Orders matching these `orderRef` prefixes are ignored. |

---

### Portfolio Observations (`observations`)

Settings for periodic portfolio evaluation:

| Parameter | Type | Required / Default | Constraints / Allowed Values | Description |
| :--- | :--- | :--- | :--- | :--- |
| `enabled` | `boolean` | Optional (`false`) | `true`, `false` | Master toggle for scheduled portfolio scans. |
| `schedule` | `string` | Optional (`"0 9,15 * * 1-5"`) | Valid 5-part cron expression | Cron schedule for running observation rules. |
| `timezone` | `string` | Optional (`"America/New_York"`) | Valid IANA timezone name | Timezone in which `schedule` is evaluated. |
| `rules` | `list[object]` | Optional (`[]`) | List of rule definitions | List of observation rule objects to evaluate. |

---

### Credential Configuration (`.secret-config.yaml`)



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
name: "Production Order Notifier"
connection: default
silent_startup: false

notifications:
  enabled: true
  notify_on_placement: true
  notify_on_status_change: true
  notify_on_cancellation: true
  notify_on_fill: true
  notify_on_price_adjustments: false
  
  filter_accounts: []
  include_order_refs: []
  exclude_order_refs:
    - "verify_*"

observations:
  enabled: false
  schedule: "0 9,15 * * 1-5"
  timezone: "America/New_York"
  rules: []
```

---

## 4. Alert Output Samples

### 🟢 Order Submitted
```text
🟢 Order Submitted
Account: U12345678
Contract: SPX Combo (3 legs)
Action: BUY 1 BAG @ 2.45
Order ID: 1042
Order Ref: strategy_30DTE_butterfly_001
Legs:
  • #1: BUY 1x conId 7248101
  • #2: SELL 2x conId 7248102
  • #3: BUY 1x conId 7248103
Time: 2026-10-29 10:30:15 EDT
```

### 🎯 Order Executed (Fill)
```text
🎯 Order Executed
Account: U12345678
Contract: SPX Combo (3 legs)
Fill: BOT 1 @ 2.40
Progress: 1 / 1 (Filled)
Order Ref: strategy_30DTE_butterfly_001
Order ID: 1042
Commission: 4.20 USD
Time: 2026-10-29 10:30:42 EDT
```

### 🔴 Order Cancelled
```text
🔴 Order Cancelled
Account: U12345678
Contract: SPX Combo (3 legs)
Action: BUY 1 BAG
Order ID: 1042
Order Ref: strategy_30DTE_butterfly_001
Reason: Cancelled by user or strategy adjustment
Time: 2026-10-29 10:31:05 EDT
```

Telegram credentials must be configured in `config/{env}/.secret-config.yaml`:

| Key Path | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `telegram.connections.<connection>.token` | `string` | **Required** | Telegram bot token obtained from @BotFather (e.g., `"123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ"`). |
| `telegram.connections.<connection>.chat_id` | `string` | **Required** | Target user ID or channel/group ID (e.g., `"123456789"` or `"-1001234567890"`). |
