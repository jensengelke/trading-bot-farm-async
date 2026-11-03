# Verify Bot (`verify`)

## 1. Overview

The `verify` bot is a connectivity and market data validation bot for the Trading Bot Farm framework. Its primary purpose is to test connectivity to Interactive Brokers (IB Gateway or TWS) and verify that market data permissions, contract definitions, and market data retrieval operate correctly across various asset classes before deploying automated trading strategies.

### Capabilities by Asset Class:
- **Stocks (`stock`)**: Resolves underlying equity contracts and retrieves/logs the most recent 1-minute historical candlestick bar (OHLCV).
- **Futures (`future`)**: Resolves futures contracts (targeting the active front-month contract if not explicitly specified) and retrieves/logs the latest 1-minute historical candlestick bar.
- **Options (`option`)**: Performs automated chain discovery and contract qualification for both equity options (e.g., TSLA) and cash-settled index options (e.g., DAX on EUREX/DTB, SPX on CBOE). It dynamically discovers this Friday's expiration (or nearest upcoming Friday with holiday rollbacks), calculates the at-the-money (ATM) strike based on live underlying spot prices, subscribes to market data, and logs/prints live bid and ask quotes.

The bot is designed to run on startup or on demand, execute its verification checks asynchronously with timeout protection, and exit cleanly without placing live orders.

---

## 2. Configuration Parameters

The configuration file for a Verify bot is located in an environment configuration directory (e.g., `config/demo/verify_options.yaml`, `config/live/verify_stocks.yaml`). The schema is strictly validated at startup using Pydantic (`extra: "forbid"`).

### Root Configuration Parameters

| Parameter | Type | Required / Default | Constraints / Allowed Values | Description |
| :--- | :--- | :--- | :--- | :--- |
| `type` | `string` | **Required** | Must be `"verify"` | Bot type identifier. Dispatches instantiation to `bots.verify.bot.Bot`. |
| `security_type` | `string` | Optional (`"stock"`) | `"stock"`, `"option"`, `"future"` | Global asset class for the symbols list when not overridden at the symbol level. |
| `symbols` | `list[object]` | **Required** | Minimum 1 item | List of symbol configurations to verify. See [Symbol Configuration](#symbol-configuration) below. |

---

### Symbol Configuration (`symbols[]`)

Each item in the `symbols` array defines a target instrument to verify:

| Parameter | Type | Required / Default | Constraints / Allowed Values | Description |
| :--- | :--- | :--- | :--- | :--- |
| `ticker` | `string` | **Required** | Non-empty string (e.g., `"IBM"`, `"TSLA"`, `"SPX"`, `"MNQ"`) | Ticker symbol of the asset or underlying. |
| `exchange` | `string` | Optional (`"SMART"`) | Valid IB exchange (e.g., `"SMART"`, `"CBOE"`, `"EUREX"`, `"CME"`, `"COMEX"`, `"IBIS"`) | Exchange routing destination for contract definition and market data. |
| `currency` | `string` | Optional (`"USD"`) | ISO 3-letter currency code (e.g., `"USD"`, `"EUR"`) | Denominated currency of the contract. |
| `right` | `string` | Optional (`"C"`) | `"C"`, `"P"` | Option right when `security_type` is `"option"`. `"C"` for Call, `"P"` for Put. |
| `security_type` | `string` | Optional (`null`) | `"stock"`, `"index"`, `"future"`, or `null` | Optional explicit underlying security type hint. If omitted (`null`), the bot automatically detects whether the underlying is a stock or index (e.g., resolves `DAX` on EUREX as `IND`, `SPX` on CBOE as `IND`, and `TSLA` on SMART as `STK`). |

---

## 3. Configuration Examples

### Example 1: Stocks Verification (`verify_stocks.yaml`)
```yaml
type: verify
security_type: stock

symbols:
  - ticker: IBM
    exchange: SMART
    currency: USD
  - ticker: ENR
    exchange: IBIS
    currency: EUR
```

### Example 2: Options & Index Options Verification (`verify_options.yaml`)
```yaml
type: verify
security_type: option

symbols:
  - ticker: TSLA
    exchange: SMART
    currency: USD
    right: C
    # Underlying auto-detected as stock (STK); finds this Friday's ATM Call

  - ticker: DAX
    exchange: EUREX
    currency: EUR
    right: C
    # Underlying auto-detected as index (IND) on EUREX/DTB

  - ticker: SPX
    exchange: CBOE
    currency: USD
    right: P
    # Underlying auto-detected as index (IND) on CBOE
```

### Example 3: Futures Verification (`verify_futures.yaml`)
```yaml
type: verify
security_type: future

symbols:
  - ticker: MNQ
    exchange: CME
    currency: USD
    # Automatically resolves active front-month Micro E-mini Nasdaq-100 future

  - ticker: MGC
    exchange: COMEX
    currency: USD
    # Automatically resolves active front-month Micro Gold future
```

---

## 4. Operational Behavior

1. **Shared IB Connection**: Connects through the framework's singleton `IBConnectionManager`.
2. **Timeout Guards**: All network calls (contract qualification, option parameters retrieval, historical data, and market data subscriptions) are wrapped in `asyncio.wait_for` timeouts (default 5.0s to 10.0s) to prevent hanging during gateway connectivity delays.
3. **Friday Expiration Targeting**: When checking options, the bot dynamically calculates the target expiration for the current week's Friday. If the market is closed or Friday has passed, it targets the following Friday.
4. **ATM Strike Selection**: Subscribes to live spot prices of the underlying asset and selects the option strike nearest to the spot price from the available strike chain.
