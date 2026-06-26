# Butterfly Bot Strategy Documentation

## Overview

The Butterfly bot is a highly configurable trading bot that places butterfly spreads on various underlyings at scheduled times. It supports flexible leg configuration, intelligent strike selection, mid-price monitoring, and bracket orders for risk management.

## Strategy Description

A butterfly spread is a neutral options strategy that profits from low volatility in the underlying asset. It consists of multiple option legs with different strikes but the same expiration date. The classic butterfly has three strikes:

- **Body (Center)**: Two options at the middle strike (typically sold)
- **Wings**: One option at a higher strike and one at a lower strike (typically bought)

The butterfly bot generalizes this concept, allowing you to configure any number of legs with flexible strike selection and position sizing.

## Key Features

### 1. Flexible Underlying Support
- **Index Options**: Trade on indices like SPX, NDX, RUT
- **Stock Options**: Trade on individual stocks like AAPL, TSLA, etc.
- **Configurable Exchange**: Specify exchange for underlying (CBOE, SMART, etc.)

### 2. Intelligent Expiration Selection
- **DTE Configuration**: Specify desired days to expiration (0-365)
- **Exact or Flexible**: Choose exact DTE or search for closest available expiration
- **Smart Search**: If exact DTE not available, searches +1, -1, +2, -2, etc. up to 3 days

### 3. Flexible Scheduling
- **Multiple Entry Days**: Configure which days of the week to trade (e.g., Monday, Wednesday, Friday)
- **Multiple Entry Times**: Configure multiple times per day (e.g., 09:30, 14:00)
- **Timezone Support**: Specify timezone for entry times

### 4. Configurable Leg Structure
Each leg can be configured with:
- **Name**: For logging and reference
- **Right**: Call or Put
- **Ratio**: Number of contracts (negative for sell, positive for buy)
- **Strike Selection**: Relative to underlying price or another leg
- **Strike Offset**: Points above/below reference

### 5. Mid-Price Monitoring
- **Configurable Period**: Monitor mid prices for specified seconds (default: 10)
- **Mean Calculation**: Calculate mean of mid prices over monitoring period
- **Smart Pricing**: Use mean mid price as initial limit price

### 6. Premium Constraints
- **Min Premium**: Minimum acceptable premium (credit or debit)
- **Max Premium**: Maximum acceptable premium (credit or debit)
- **Automatic Adjustment**: Prices adjusted to stay within constraints

### 7. Price Adjustments
- **Configurable Attempts**: Specify max number of price adjustments (default: 4)
- **Smart Direction**: Adjusts toward zero (reduces credit or debit)
- **30-Second Intervals**: Waits 30 seconds between adjustments

### 8. Bracket Orders
- **Stop Loss**: Configurable stop loss factor
- **Take Profit**: Configurable take profit factor
- **GTC Orders**: Good-till-cancel for automatic exit management

## Configuration

### Basic Configuration

```yaml
type: butterfly

# Underlying configuration
underlying_type: Index  # or "Stock"
underlying_symbol: SPX
underlying_exchange: CBOE  # or "SMART"

# Expiration configuration
dte: 0  # Days to expiration
use_exact_dte: false  # If true, only exact DTE is used

# Scheduling
entry_days:
  - Tuesday
  - Wednesday
  - Thursday
  - Friday
entry_times:
  - "09:45"
  - "14:00"
timezone: America/New_York

# Position sizing
num_contracts: 1
```

### Leg Configuration Examples

#### Example 1: Balanced Short Call Butterfly

This configuration creates a balanced short call butterfly:
- Sell 2 calls at current price
- Buy 1 call 25 points above
- Buy 1 call 25 points below

```yaml
legs:
  - name: center
    right: call
    ratio: -2
    strike_selection: underlying_offset
    strike_offset: 0
  
  - name: top
    right: call
    ratio: 1
    strike_selection: leg_offset
    strike_selection_parent: center
    strike_offset: 25
  
  - name: bottom
    right: call
    ratio: 1
    strike_selection: leg_offset
    strike_selection_parent: center
    strike_offset: -25
```

**Explanation:**
- **Center leg**: Sells 2 calls at the current underlying price (offset 0)
- **Top leg**: Buys 1 call 25 points above the center leg strike
- **Bottom leg**: Buys 1 call 25 points below the center leg strike

**P&L Profile:**
- Maximum profit: Premium received (occurs if underlying stays at center strike)
- Maximum loss: Width of wing minus premium (occurs if underlying moves beyond wings)
- Breakeven: Center strike ± (wing width - premium)

#### Example 2: Balanced Long Put Butterfly

This configuration creates a balanced long put butterfly:
- Buy 1 put 25 points above current price
- Sell 2 puts at current price
- Buy 1 put 25 points below current price

```yaml
legs:
  - name: center
    right: put
    ratio: -2
    strike_selection: underlying_offset
    strike_offset: 0
  
  - name: top
    right: put
    ratio: 1
    strike_selection: leg_offset
    strike_selection_parent: center
    strike_offset: 25
  
  - name: bottom
    right: put
    ratio: 1
    strike_selection: leg_offset
    strike_selection_parent: center
    strike_offset: -25
```

#### Example 3: Asymmetric Butterfly

This configuration creates an asymmetric butterfly with wider upside:
- Sell 2 calls at current price
- Buy 1 call 50 points above
- Buy 1 call 25 points below

```yaml
legs:
  - name: center
    right: call
    ratio: -2
    strike_selection: underlying_offset
    strike_offset: 0
  
  - name: top
    right: call
    ratio: 1
    strike_selection: leg_offset
    strike_selection_parent: center
    strike_offset: 50
  
  - name: bottom
    right: call
    ratio: 1
    strike_selection: leg_offset
    strike_selection_parent: center
    strike_offset: -25
```

#### Example 4: Iron Butterfly (4 Legs)

This configuration creates an iron butterfly:
- Sell 1 call at current price
- Sell 1 put at current price
- Buy 1 call 25 points above
- Buy 1 put 25 points below

```yaml
legs:
  - name: short_call
    right: call
    ratio: -1
    strike_selection: underlying_offset
    strike_offset: 0
  
  - name: short_put
    right: put
    ratio: -1
    strike_selection: underlying_offset
    strike_offset: 0
  
  - name: long_call
    right: call
    ratio: 1
    strike_selection: leg_offset
    strike_selection_parent: short_call
    strike_offset: 25
  
  - name: long_put
    right: put
    ratio: 1
    strike_selection: leg_offset
    strike_selection_parent: short_put
    strike_offset: -25
```

**Note:** For iron butterfly, you need to remove the validation that requires all legs to have the same right. This is currently enforced in the config schema.

### Pricing Configuration

```yaml
# Mid-price monitoring
mid_price_monitoring_period: 10  # seconds

# Premium constraints (optional)
min_premium: -2.50  # Minimum credit (or maximum debit if positive)
max_premium: -0.50  # Maximum credit (or minimum debit if positive)

# Price adjustments
max_price_adjustments: 4  # Number of attempts to get filled
```

**Premium Constraint Examples:**

For a **credit spread** (negative premium):
- `min_premium: -2.50` means minimum credit of $2.50
- `max_premium: -0.50` means maximum credit of $0.50
- If calculated price is -3.00, it will be adjusted to -2.50
- If calculated price is -0.30, it will be adjusted to -0.50

For a **debit spread** (positive premium):
- `min_premium: 0.50` means minimum debit of $0.50
- `max_premium: 2.50` means maximum debit of $2.50
- If calculated price is 0.30, it will be adjusted to 0.50
- If calculated price is 3.00, it will be adjusted to 2.50

### Bracket Orders Configuration

```yaml
# Bracket orders (optional)
stoploss_factor: 1.5      # Stop loss multiplier
takeprofit_factor: 0.2    # Take profit multiplier
```

**Bracket Order Calculation:**

For a **credit spread** (initial fill at -2.00):
- `stoploss_factor: 1.5` → Stop loss at -2.00 × (1 + 1.5) = -5.00
  - Triggers if position value reaches -5.00 (loss of $3.00)
- `takeprofit_factor: 0.2` → Take profit at -2.00 × 0.2 = -0.40
  - Triggers if position value reaches -0.40 (profit of $1.60)

For a **debit spread** (initial fill at 2.00):
- `stoploss_factor: 0.2` → Stop loss at 2.00 × 0.2 = 0.40
  - Triggers if position value reaches 0.40 (loss of $1.60)
- `takeprofit_factor: 1.6` → Take profit at 2.00 × (1 + 1.6) = 5.20
  - Triggers if position value reaches 5.20 (profit of $3.20)

## Complete Configuration Example

Here's a complete configuration for a 0 DTE SPX short call butterfly:

```yaml
# Butterfly Bot Configuration - 0 DTE SPX Short Call Butterfly
type: butterfly

# Underlying: SPX Index
underlying_type: Index
underlying_symbol: SPX
underlying_exchange: CBOE

# Expiration: 0 DTE (same day expiration)
dte: 0
use_exact_dte: false  # Allow closest expiration if exact not available

# Schedule: Trade Tuesday-Friday at 9:45 AM ET
entry_days:
  - Tuesday
  - Wednesday
  - Thursday
  - Friday
entry_times:
  - "09:45"
timezone: America/New_York

# Butterfly Structure: Balanced short call butterfly
# Sell 2 calls at-the-money, buy 1 call 25 points above and below
legs:
  - name: center
    right: call
    ratio: -2
    strike_selection: underlying_offset
    strike_offset: 0
  
  - name: top_wing
    right: call
    ratio: 1
    strike_selection: leg_offset
    strike_selection_parent: center
    strike_offset: 25
  
  - name: bottom_wing
    right: call
    ratio: 1
    strike_selection: leg_offset
    strike_selection_parent: center
    strike_offset: -25

# Position Sizing
num_contracts: 1

# Pricing
mid_price_monitoring_period: 10  # Monitor for 10 seconds
min_premium: -2.00  # Minimum credit of $2.00
max_premium: -0.50  # Maximum credit of $0.50
max_price_adjustments: 4

# Bracket Orders
stoploss_factor: 1.5      # Stop loss at 2.5x initial credit
takeprofit_factor: 0.2    # Take profit at 20% of initial credit
```

## Strike Selection Methods

### Method 1: Underlying Offset

Selects strike relative to current underlying price.

```yaml
strike_selection: underlying_offset
strike_offset: 0  # At-the-money
```

**Examples:**
- `strike_offset: 0` → At current price
- `strike_offset: 25` → 25 points above current price
- `strike_offset: -25` → 25 points below current price

### Method 2: Leg Offset

Selects strike relative to another leg's strike.

```yaml
strike_selection: leg_offset
strike_selection_parent: center
strike_offset: 25  # 25 points above parent leg
```

**Examples:**
- `strike_offset: 25` → 25 points above parent leg
- `strike_offset: -25` → 25 points below parent leg

## Bot Behavior

### Startup Sequence

1. **Validate Configuration**: Pydantic validates all configuration fields
2. **Connect to IB**: Obtains shared IB connection from framework
3. **Calculate Next Execution**: Determines next scheduled entry time
4. **Wait**: Sleeps until next execution time (checks every 10 seconds for stop request)

### Execution Sequence

1. **Get Underlying Contract**: Qualifies Index or Stock contract
2. **Get Current Price**: Retrieves current underlying price
3. **Find Expiration**: Searches for appropriate expiration based on DTE
4. **Determine Strikes**: Calculates strikes for all legs based on configuration
5. **Create Option Contracts**: Creates and qualifies option contracts for all legs
6. **Monitor Mid Prices**: Subscribes to market data and collects mid prices
7. **Calculate Limit Price**: Computes mean mid price over monitoring period
8. **Apply Constraints**: Adjusts price to respect min/max premium constraints
9. **Place Order**: Places combo order with calculated limit price
10. **Monitor Fill**: Waits 30 seconds, adjusts price if not filled (up to max adjustments)
11. **Place Brackets**: If filled, places stop loss and take profit orders

### Price Adjustment Logic

If order is not filled after 30 seconds:

1. **Determine Direction**:
   - Credit spread (negative premium): Increase price by $0.05 (reduce credit)
   - Debit spread (positive premium): Decrease price by $0.05 (reduce debit)

2. **Apply Constraints**: Ensure adjusted price respects min/max premium

3. **Modify Order**: Update order with new limit price

4. **Repeat**: Continue for up to `max_price_adjustments` attempts

5. **Cancel**: If max adjustments reached, cancel order

## Risk Management

### Position Sizing
- Configure `num_contracts` to control position size
- Start with 1 contract and scale up as you gain confidence

### Premium Constraints
- Use `min_premium` and `max_premium` to avoid unfavorable fills
- For credit spreads, ensure minimum credit justifies risk
- For debit spreads, ensure maximum debit doesn't exceed profit potential

### Bracket Orders
- **Stop Loss**: Protects against adverse moves
  - Set factor based on risk tolerance (e.g., 1.5 = exit at 2.5x initial credit)
- **Take Profit**: Locks in profits
  - Set factor based on profit target (e.g., 0.2 = exit at 20% of initial credit)

### Expiration Management
- **0 DTE**: High gamma risk, requires close monitoring
- **7-30 DTE**: More time value, lower gamma risk
- **Use exact DTE**: Ensures consistent strategy execution

## Logging

The bot provides comprehensive logging at three levels:

### Standard Log (INFO+)
- Execution schedule
- Strike selection
- Order placement
- Fill status
- Bracket order placement

### Error Log (WARNING+)
- Configuration errors
- Connection failures
- Order failures
- Unexpected conditions

### Trace Log (DEBUG+)
- Detailed execution flow
- Mid price samples
- Strike calculations
- Method entry/exit

## Troubleshooting

### Bot Not Executing
- Check `entry_days` and `entry_times` configuration
- Verify timezone is correct
- Check logs for next scheduled execution time

### No Suitable Expiration Found
- If `use_exact_dte: true`, ensure exact DTE exists
- If `use_exact_dte: false`, check if any expiration within 30 days
- Verify underlying has option chains available

### Order Not Filling
- Check `max_price_adjustments` - increase if needed
- Review `min_premium` and `max_premium` constraints
- Check market conditions (volatility, liquidity)
- Review trace logs for price adjustment attempts

### Invalid Strike Selection
- Ensure parent legs are defined before child legs
- Verify strike offsets are reasonable for underlying
- Check that strikes are available in option chain

## Best Practices

1. **Start Small**: Begin with 1 contract and paper trading
2. **Test Configuration**: Validate configuration before live trading
3. **Monitor Logs**: Review logs regularly to understand bot behavior
4. **Adjust Constraints**: Fine-tune premium constraints based on market conditions
5. **Use Brackets**: Always configure stop loss for risk management
6. **Review Fills**: Check fill prices to ensure reasonable execution
7. **Backtest**: Test strategy with historical data before live deployment

## Example Configurations

### Conservative 0 DTE SPX Butterfly
```yaml
type: butterfly
underlying_type: Index
underlying_symbol: SPX
underlying_exchange: CBOE
dte: 0
use_exact_dte: false
entry_days: [Tuesday, Wednesday, Thursday, Friday]
entry_times: ["09:45"]
timezone: America/New_York
legs:
  - {name: center, right: call, ratio: -2, strike_selection: underlying_offset, strike_offset: 0}
  - {name: top, right: call, ratio: 1, strike_selection: leg_offset, strike_selection_parent: center, strike_offset: 25}
  - {name: bottom, right: call, ratio: 1, strike_selection: leg_offset, strike_selection_parent: center, strike_offset: -25}
num_contracts: 1
mid_price_monitoring_period: 10
min_premium: -2.00
max_premium: -0.50
max_price_adjustments: 4
stoploss_factor: 1.5
takeprofit_factor: 0.2
```

### Aggressive 7 DTE Stock Butterfly
```yaml
type: butterfly
underlying_type: Stock
underlying_symbol: AAPL
underlying_exchange: SMART
dte: 7
use_exact_dte: false
entry_days: [Monday, Wednesday, Friday]
entry_times: ["10:00", "14:00"]
timezone: America/New_York
legs:
  - {name: center, right: put, ratio: -2, strike_selection: underlying_offset, strike_offset: 0}
  - {name: top, right: put, ratio: 1, strike_selection: leg_offset, strike_selection_parent: center, strike_offset: 10}
  - {name: bottom, right: put, ratio: 1, strike_selection: leg_offset, strike_selection_parent: center, strike_offset: -10}
num_contracts: 2
mid_price_monitoring_period: 15
min_premium: -1.50
max_premium: -0.30
max_price_adjustments: 6
stoploss_factor: 2.0
takeprofit_factor: 0.15
```

## Conclusion

The Butterfly bot provides a flexible framework for trading butterfly spreads with intelligent automation. By carefully configuring the legs, scheduling, pricing, and risk management parameters, you can implement a wide variety of butterfly strategies across different underlyings and market conditions.

Remember to always test configurations thoroughly in paper trading before deploying to live trading, and monitor bot behavior closely, especially when first starting out.
