# Entry Conditions

## Overview

Entry conditions allow you to define criteria that must be met before a strategy bot places a trade. This feature enables you to implement market-based filters similar to those used in the FKK strategy, ensuring trades are only placed when specific market conditions are favorable.

## Configuration

Entry conditions are configured in the strategy bot's YAML configuration file under the `entry_conditions` section. This is an optional array - if not specified, the bot will place trades at the scheduled times without any additional conditions.

### Basic Structure

```yaml
entry_conditions:
  - name: condition_name
    type: condition_type
    # Additional parameters depend on the type
```

**All conditions must be met** for a trade to be placed. If any condition fails, the trade is skipped.

## Available Condition Types

### 1. SMA (Simple Moving Average)

Compares the current price to a simple moving average.

**Parameters:**
- `name` (string, required): Name for logging purposes
- `type` (string, required): Must be `"SMA"`
- `period` (integer, required): Number of days for SMA calculation (e.g., 5 for 5-day SMA)
- `operator` (string, required): Comparison operator - one of: `>`, `>=`, `<`, `<=`, `==`

**How it works:**
The SMA is calculated as: `(sum of last (period-1) daily closes + current price) / period`

This means for a 5-day SMA, it uses the last 4 daily closes plus the current intraday price.

**Example:**
```yaml
entry_conditions:
  - name: above_5day_sma
    type: SMA
    period: 5
    operator: ">="
```

This condition is met when the current price is at or above the 5-day SMA.

### 2. Underlying Intraday Move

Checks if the underlying has moved by a certain percentage from today's open.

**Parameters:**
- `name` (string, required): Name for logging purposes
- `type` (string, required): Must be `"underlying_intraday_move"`
- `threshold` (float, required): Percentage threshold as a decimal (e.g., 0.003 for 0.3%, 0.005 for 0.5%)
- `operator` (string, required): Comparison operator - one of: `>`, `>=`, `<`, `<=`

**How it works:**
Compares the current price to today's open price. The threshold is applied as a percentage of the open price.

**Example:**
```yaml
entry_conditions:
  - name: up_at_least_0.3_percent
    type: underlying_intraday_move
    operator: ">="
    threshold: 0.003
```

This condition is met when the current price is at least 0.3% above today's open.

## Complete Example: FKK-Style Strategy

Here's a complete example that replicates the FKK bot's entry logic using the strategy bot with entry conditions:

```yaml
type: strategy

# Entry conditions - both must be met
entry_conditions:
  - name: moving_average
    type: SMA
    period: 5
    operator: ">="
  - name: intraday_move
    type: underlying_intraday_move
    operator: ">="
    threshold: 0.003

# Underlying configuration
underlying_type: Index
underlying_symbol: SPX
underlying_exchange: CBOE

# Expiration configuration
dte: 0  # 0 DTE
use_exact_dte: true

# Scheduling
entry_days:
  - Tuesday
  - Wednesday
  - Thursday
  - Friday

entry_times:
  - "14:13"

timezone: America/New_York

# Bull Put Spread
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
mid_price_monitoring_period: 10
max_price_adjustments: 4
```

## How Entry Conditions Are Evaluated

1. **Timing**: Entry conditions are checked at the scheduled execution time (defined by `entry_days` and `entry_times`)

2. **Evaluation Order**: Conditions are evaluated in the order they appear in the configuration

3. **Short-Circuit Logic**: If any condition fails, evaluation stops immediately and the trade is skipped

4. **Logging**: Each condition evaluation is logged with detailed information:
   - Current values (price, SMA, threshold, etc.)
   - Comparison result
   - Whether the condition passed or failed

5. **No Conditions**: If `entry_conditions` is not specified or is an empty array, the bot will place trades at every scheduled time without additional checks

## Logging Example

When entry conditions are evaluated, you'll see log entries like:

```
INFO: Evaluating 2 entry conditions
INFO: Condition 'moving_average': SMA5=5850.23, Current price=5862.45
INFO: Condition 'moving_average': 5862.45 >= 5850.23 = True
INFO: Condition 'intraday_move': Today's open=5845.00, Current=5862.45, Move=0.30%, Threshold=0.30%
INFO: Condition 'intraday_move': 5862.45 >= 5862.54 = False
INFO: Entry condition 'intraday_move' NOT met - entry blocked
INFO: Entry conditions not met - skipping trade
```

## Adding New Condition Types

The entry condition system is extensible. To add a new condition type:

1. Create a new class in `framework/entry_conditions.py` that extends `EntryCondition`
2. Implement the `evaluate()` method
3. Register the new type in `EntryConditionEvaluator.CONDITION_TYPES`
4. Add the new type to the `EntryConditionConfig` Pydantic model in `bots/strategy/config.py`
5. Add any type-specific fields with appropriate validators

## Best Practices

1. **Descriptive Names**: Use clear, descriptive names for your conditions to make logs easier to read

2. **Threshold Selection**: Test your thresholds with historical data to ensure they're appropriate for your strategy

3. **Operator Choice**: 
   - Use `>=` instead of `>` for inclusive conditions (recommended for most cases)
   - Use `>` for strict conditions where you want to exclude exact matches

4. **Condition Order**: Place faster-to-evaluate conditions first to take advantage of short-circuit evaluation

5. **Testing**: Test your configuration with paper trading before using it live

## Troubleshooting

**Condition always fails:**
- Check the operator direction (e.g., `>=` vs `<=`)
- Verify threshold values are in the correct format (decimal, not percentage)
- Review logs to see actual values being compared

**No historical data:**
- Ensure the market is open when conditions are evaluated
- Check that the underlying symbol is correct
- Verify IB connection has market data permissions

**Configuration validation errors:**
- Ensure all required fields are present for each condition type
- Check that field values are within valid ranges
- Verify the `type` field matches exactly (case-sensitive)
