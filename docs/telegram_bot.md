# Telegram Notification Bot (`telegram`)

## 1. Overview

The `telegram` bot type provides real-time notifications for automated trading operations, order state transitions, and execution fills across the Trading Bot Farm. It runs as an asynchronous observer within the framework's single event loop and connects to the shared Interactive Brokers connection managed by `IBConnectionManager`.

### Key Features
- **Real-Time Order Monitoring**: Subscribes to global order callbacks on IB Gateway/TWS (`openOrderEvent`, `orderStatusEvent`, `execDetailsEvent`).
- **Rich HTML Alerts**: Formats alerts with emojis, contract summaries (stocks, options, futures, combo spreads), prices, quantities, order references (`orderRef`), and commissions.
- **Two-Tier Configuration**: Sensitive bot tokens and chat IDs remain strictly in `.secret-config.yaml` (git-ignored), while notification preferences are defined in the bot YAML file.
- **Asynchronous Transport**: Non-blocking delivery using `asyncio.to_thread` with standard library `urllib`, including rate-limit backoff and automatic message splitting.
- **Debounced Updates**: Suppresses duplicate notifications and transient status fluctuations.
- **Scheduled Observations (Phase 2 Ready)**: Architecture ready for periodic portfolio scans (earnings dates, Greek delta drift).

---

## 2. Configuration Setup

### Step 1: Obtain Telegram Bot Token & Chat ID
1. Open Telegram and message **@BotFather**.
2. Run `/newbot` and follow the prompts to choose a bot name and username.
3. Save the HTTP API **token** (e.g., `123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ`).
4. To get your **chat ID**:
   - For direct messages: Start a chat with `@userinfobot` or send a message to your new bot and visit `https://api.telegram.org/bot<TOKEN>/getUpdates` to inspect `message.chat.id`.
   - For groups/channels: Add the bot as an administrator to your group or channel, post a message, and check `getUpdates` for the negative channel ID (e.g., `-1001234567890`).

### Step 2: Add Credentials to `.secret-config.yaml`
Add your Telegram credentials to your environment's secret configuration (e.g. `config/live/.secret-config.yaml` or `config/demo/.secret-config.yaml`):

```yaml
telegram:
  connections:
    default:
      token: "YOUR_TELEGRAM_BOT_TOKEN"
      chat_id: "YOUR_TELEGRAM_CHAT_ID"
```

### Step 3: Configure Bot in YAML
Create or rename `config/{env}/telegram_notifier.yaml`:

```yaml
name: "Telegram Order Notifier"
type: "telegram"
connection: "default"     # Matches connection name in .secret-config.yaml
silent_startup: false

notifications:
  enabled: true
  notify_on_placement: true        # Alert when new orders are placed
  notify_on_status_change: true    # Alert when status changes (Submitted, etc.)
  notify_on_cancellation: true     # Alert when orders are cancelled
  notify_on_fill: true             # Alert on execution fills
  notify_on_price_adjustments: false
  
  # Filtering
  filter_accounts: []              # Empty = monitor all accounts
  include_order_refs: []           # Optional: filter by orderRef prefix
  exclude_order_refs: ["verify_*"] # Optional: ignore verification bots
```


---

## 3. Alert Formats & Examples

### 🟢 Order Submitted
Sent when a new order is accepted and submitted to IBKR:
```html
🟢 Order Submitted
Account: U22220131
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
Sent when an execution fill is received with price and commissions:
```html
🎯 Order Executed
Account: U22220131
Contract: SPX Combo (3 legs)
Fill: BOT 1 @ 2.40
Progress: 1 / 1 (Filled)
Order Ref: strategy_30DTE_butterfly_001
Order ID: 1042
Commission: 4.20 USD
Time: 2026-10-29 10:30:42 EDT
```

### 🔴 Order Cancelled
Sent when an order is cancelled or invalidated:
```html
🔴 Order Cancelled
Account: U22220131
Contract: SPX Combo (3 legs)
Action: BUY 1 BAG
Order ID: 1042
Order Ref: strategy_30DTE_butterfly_001
Reason: Cancelled by user or strategy adjustment
Time: 2026-10-29 10:31:05 EDT
```

---

## 4. Phase 2: Portfolio Observations (Roadmap)

The bot configuration includes an `observations` block designed for scheduled portfolio analysis:
- **Greek Delta Drift**: Identifying Bull Put Spreads or Iron Condors where short leg delta rises above threshold (e.g. `> 0.38`).
- **Earnings Calendar**: Cross-referencing active equity positions with upcoming earnings releases scheduled within the current week.
- **Expiration Warnings**: Alerts on positions expiring within 0-2 days to manage assignment risk.
