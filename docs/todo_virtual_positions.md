# virtual positions

## questions
I confused myself:
I was trading a SPX butterfly with expiry on July 2nd: 
-2 Call at 7520
+1 Call at 7545
+1 Call at 7495

I paid 1,20 USD Debit. I created a take profit order at 1,25x initial debit, which was rounded 1,50 USD 

Later, I opened a 2nd butterfly on SPX, also with expiry on July 2nd:
-2 Call at 7495
+1 Call at 7250
+1 Call at 7460

I paid 1,30 USD Debit and wanted to place a take profit at 1,25x initial debit at rounded 1.70 USD.

The overlapping strikes are cancelling out each other. The resulting position in portfolio is 
+1 call at 7470
-1 call at 7495
-1 call at 7520
+1 call at 7545

I cancelled my first take profit, because the sizes and strikes no longer matched.

Am I right to assume that I should have kept my initial take profit and created a new take profit for the 2nd position, which is the opposite of the first position?

My goal is to build an automated trading system at interactive brokers using their python API. I wonder how to deal with situation above.

Looking at the portfolio, I have no way of telling which positions are actually there and which take profit to set. 

How are other systems doing that?

I think it is easy enough to attach my take profit orders to my opening orders to ensure they match. However, what if I close one of my positions manually? How would my bot know which take profit to cancel?

Can I use "orderRef" for this purpose?
## response

```python
import pandas as pd
df = pd.read_cols = pd.read_csv('trades.csv')
print(df.head())
print(df.info())



```

```text
                            Fin Instrument                 Symbol Action  Quantity  Price      Time      Date Exch.                       Account  Order Ref. Clearing  Unnamed: 11
0             SPX Jun18 7490/7480 Bull Put               28812380    SLD         1  -3.20  19:03:07  20260618  CBOE  ADIF8 U22220131 Jens Engelke         NaN     IBKR          NaN
1             SPX (SPXW) Jun18'26 7490 PUT  SPXW  260618P07490000    BOT         1   5.50  19:03:07  20260618  CBOE  ADIF8 U22220131 Jens Engelke         NaN      NaN          NaN
2             SPX (SPXW) Jun18'26 7480 PUT  SPXW  260618P07480000    SLD         1   2.30  19:03:07  20260618  CBOE  ADIF8 U22220131 Jens Engelke         NaN      NaN          NaN
3  SPX Jul02 7470/7495/7520 Butterfly Call               28812380    BOT         1   1.30  18:52:14  20260618  CBOE  ADIF8 U22220131 Jens Engelke         NaN     IBKR          NaN
4            SPX (SPXW) Jul02'26 7495 CALL  SPXW  260702C07495000    SLD         2  85.48  18:52:14  20260618  CBOE  ADIF8 U22220131 Jens Engelke         NaN      NaN          NaN
<class 'pandas.core.frame.DataFrame'>
RangeIndex: 28 entries, 0 to 27
Data columns (total 12 columns):
 #   Column          Non-Null Count  Dtype  
---  ------          --------------  -----  
 0   Fin Instrument  28 non-null     object 
 1   Symbol          28 non-null     object 
 2   Action          28 non-null     object 
 3   Quantity        28 non-null     int64  
 4   Price           28 non-null     float64
 5   Time            28 non-null     object 
 6   Date            28 non-null     int64  
 7   Exch.           28 non-null     object 
 8   Account         28 non-null     object 
 9   Order Ref.      0 non-null      float64
 10  Clearing        8 non-null      object 
 11  Unnamed: 11     0 non-null      float64
dtypes: float64(3), int64(2), object(7)
memory usage: 2.8+ KB
None


```

This is a classic dilemma when transitioning from manual trading (where you view trades as cohesive "strategies" like Butterflies) to a programmatic or portfolio-level view, where the broker only sees a flat bucket of individual contracts.

Let's break down your questions on how professional automated systems handle this, how you should have handled your take profits, and how to use IBKR's features like `orderRef`.

---

### 1. Should you have kept the initial take profit?

**Yes.** Physically, your portfolio now holds a combined 4-leg position (an Iron Butterfly or Condor-like structure). However, *financially and mathematically*, your two separate trades remain independent.

If you had kept both take-profit orders alive, they would look like this to IBKR:

* **Take Profit 1:** Orders to Sell (+1 @ 7495), Buy (-2 @ 7520), Sell (+1 @ 7545) for a credit of $1.50.
* **Take Profit 2:** Orders to Sell (+1 @ 7460), Buy (-2 @ 7495), Sell (+1 @ 7470) for a credit of $1.70.

Even though your net position at the 7495 strike is currently 0, if the market moved such that the first butterfly became worth $1.50, that take-profit order would execute. It would sell the 7495 contract (making your net position -1 at that strike), buy back the 7520s, and sell the 7545s. Your second butterfly would remain perfectly intact.

**Rule of thumb:** In option trading, as long as you have enough margin/capital, you can treat independent strategies as separate orders. The broker will net out the underlying inventory, but the orders will execute exactly as specified.

---

### 2. How do professional trading systems handle this? ("Virtual Positioning")

Looking at your Interactive Brokers portfolio directly (via `reqPositions()`), you will only see:

* `+1 SPX 7470 Call`
* `-1 SPX 7495 Call`
* `-1 SPX 7520 Call`
* `+1 SPX 7545 Call`

As you noted, it is impossible to reconstruct your intent just by looking at this list. This is why automated trading systems implement **Virtual Position Tracking** (or a local database/state machine).

Instead of relying on the broker to tell the bot what *strategies* it has, the bot maintains an internal database (or a dictionary in memory):

```python
# A conceptual example of how your bot's database tracks state
virtual_positions = {
    "strategy_instance_001": {
        "strategy_type": "Butterfly",
        "legs": {"7495C": 1, "7520C": -2, "7545C": 1},
        "status": "OPEN",
        "tp_order_id": 9001
    },
    "strategy_instance_002": {
        "strategy_type": "Butterfly",
        "legs": {"7460C": 1, "7495C": -2, "7470C": 1},
        "status": "OPEN",
        "tp_order_id": 9002
    }
}

```

When a fill occurs, the bot updates its local database. When it wants to calculate its profit, it checks the prices of the legs belonging to `strategy_instance_001` and evaluates them independently of `strategy_instance_002`.

---

### 3. Can you use `orderRef` for this?

**Absolutely, but with a specific limitation.** The `orderRef` (Order Reference) string is a powerful user-defined field. You can set it when you place an order (`Order.orderRef = "butterfly_001"`). As seen in your `trades.csv` header, IBKR tracks this field and returns it in execution reports and trade logs.

* **How it helps:** When your bot restarts or receives an execution notification (`execDetails`), it can look at `orderRef` to immediately know *which* virtual strategy that fill belongs to.
* **The limitation:** `orderRef` is an **Order** property, not a **Position** property. Once an option contract is inside your portfolio, IBKR strips away the `orderRef`. If you call `reqPositions()`, the individual contracts will not have an `orderRef` attached to them.

Therefore, `orderRef` is excellent for linking your opening orders to your closing orders, but it cannot replace your bot's internal database for tracking open inventory.

---

### 4. Handling Manual Interventions (The "Human element")

What happens if you get nervous and manually close a leg or a whole strategy inside TWS? How does your bot know which Take Profit order to cancel?

There are three common ways to design your bot to handle this:

#### Approach A: Attached Child Orders (Bracket Orders)

Instead of placing an opening order and then sending a separate take profit order later, you submit them together as a **Bracket Order**.

* You send a Parent Order (to open the butterfly).
* You attach a Child Order (the Take Profit) with `parentOrderId` set to the Parent's ID.
* **The benefit:** If you manually cancel or modify the parent order before it fills, IBKR handles the children automatically. However, if the parent has *already* filled and you decide to close the position manually, you must still program your bot to clean up the orphaned child order.

#### Approach B: The "State Reconciliation" Loop (Recommended)

This is how robust production bots operate. The bot continuously loops and reconciles its *Virtual Database* against the *Actual Broker Portfolio*:

1. The bot asks IBKR for the net portfolio positions (`reqPositions`).
2. The bot aggregates its internal virtual positions to see what the net portfolio *should* look like.
3. **Reconciliation:** * *Scenario:* You manually closed the first butterfly in TWS.
* *Bot Check:* The bot calculates that it *should* own a net 0 of 7520 calls. But `reqPositions` reports you actually own 0 (whereas it expected -2).
* *Action:* The bot realizes a discrepancy has occurred. It marks `strategy_instance_001` as "Manually Closed" in its database, looks up `tp_order_id: 9001`, and issues a `cancelOrder(9001)` to IBKR.



#### Approach C: Fully Automated vs. Read-Only Modes

Many algorithmic traders follow a strict rule: **If the bot opened it, the human doesn't touch it.** If you must intervene manually, you press a "Panic/Halt Bot" button inside your script which commands the bot to cancel all active take-profits and market-close all virtual legs itself, ensuring the internal database and the broker stay perfectly synced.


