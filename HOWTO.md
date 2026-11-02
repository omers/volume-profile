# Trading SPY Weekly Options with the Swing FRVP Indicator

How to use the **Swing Fixed Range Volume Profile** Pine Script ([swing_frvp.pine](swing_frvp.pine): BUY/SELL signals, Target 1 / Target 2, stop and history stats) to trade SPY options on a weekly basis.

To install it: in TradingView open the **Pine Editor**, replace its contents with `swing_frvp.pine`, click **Save**, then **Add to chart** and click the bar where the profile should start. Keep only one copy of the indicator on the chart; an older copy keeps running the old code.

Defaults are set for this plan: start 30 Mar 2026, min reward:risk 1.5, 15 bars per trade for the History stats, each signal followed for up to 40 bars for the `Bars to T1` row, a 3-bar cooldown against opposite signals, table at the bottom left. Stops, targets and the pullback signal follow the book *Volume Profile: The Insider's Guide to Trading* (page numbers below refer to it).

> This is general education, not personal financial advice. Options can lose 100% of the premium paid.

## Reading the chart

| On the chart | What it is | What it means |
|---|---|---|
| Histogram next to the price axis (teal = up volume, red = down volume) | Volume traded at each price since the start bar | Long bars = prices the market accepted; short bars = prices it passed through quickly |
| **Thick yellow line**, label `POC` | Point of Control: the price with the most volume | The "fair price". Price is pulled back to it; no edge while sitting on it |
| **Solid blue lines**, labels `VAH` / `VAL`, with light blue shading between | Value Area High / Low: the range holding 70% of the volume | The edges of value. All signals fire at these two lines |
| **Orange dashed lines**, label `HVN` | High-volume nodes: volume peaks | Support/resistance and the usual targets |
| **Gray dotted lines**, label `LVN` | Low-volume nodes inside the Value Area | Thin spots price tends to move through fast |
| Green `BUY` tag under a candle / red `SELL` tag above a candle | A signal on that candle's close | Hover over it for the reason, entry, T1, T2, stop, R:R and its result |
| Green zone with `T1` (thick green) and `T2` (dashed green) lines | Reward zone of the latest signal | Where to take profit. Drawn slightly in front of the level, since price often turns just before it |
| Red zone with `Stop (on close)` (thick red) line | Risk zone of the latest signal | Exit if a **daily candle closes** beyond it |
| Dotted red line, `Hard stop` | 1.5× the normal stop distance | Exit **immediately** if price touches it, even mid-day |
| Table, `State` row | Where price is relative to the Value Area | Sets the bias (see below) |
| Table, `Profile shape` row | The histogram's shape: D, P, b or thin | Sets which signals to expect (see below) |
| Table, `Bars to T1` row | How many bars past signals took to reach T1: the median, and the bar by which 80% of the T1 hits had arrived | How long a trade needs. The 80% figure is the time stop |
| Table, `Time stop` row | The 80% figure applied to the latest signal: which bar it's on and how many are left | Exit if T1 isn't reached by then |
| Table, `Filtered (last bar)` row | A setup that matched the rules on the last bar but was dropped (R:R below the minimum, or cooldown) | No trade. The setup is used up: touching the same level next bar does not fire it again |
| Table, `Armed next bar` row | Pullback levels (signals 7–8) the next bar can fire on at the first touch, nearest first, with T1 and R:R | The only levels a pullback signal can come from next bar. A level that isn't listed can't fire one, because the rule needs the previous candle fully beyond it |

### Bias from the `State` row

| State | Price location | Bias | Signals to favor |
|---|---|---|---|
| Above value (teal) | Close above VAH | Uptrend: value is moving up | BUY on a retest of VAH that holds |
| Inside value (gray) | Close between VAL and VAH | Range: price rotates between the edges | BUY near VAL, SELL near VAH, target the POC |
| Below value (red) | Close below VAL | Downtrend: value is moving down | SELL on a retest of VAL that fails |

### Profile shape (book pp. 54–58)

| Shape | How the script detects it | What it means | Signals it allows |
|---|---|---|---|
| **D** (gray) | POC in the middle 20% of the range | Balance: buyers and sellers agree on price | Value Area signals 1–6 only. Buy near VAL, sell near VAH, the POC is the target |
| **P** (teal) | POC in the top 40% of the range | Aggressive buyers, then rotation at the top | Signals 1–6, plus **pullback BUYs** at the POC / HVNs |
| **b** (red) | POC in the bottom 40% of the range | Aggressive sellers, then rotation at the bottom | Signals 1–6, plus **pullback SELLs** at the POC / HVNs |
| **Thin** (teal or red) | The POC row holds less than 2× an average row: no big volume area | A strong trend; the HVNs are the clusters where the trend paused | Signals 1–6, plus pullback signals in the trend's direction |

## Buy and sell rules

Signals 1–6 are checked on the **closed** daily candle and enter at the close. Signals 7–8 enter **at the level, on the first touch**, with no confirmation candle (book pp. 64, 69). When several match, the first one in the table wins.

| # | Signal | Rule (on the signal candle) | How it looks on the chart | Target 1 / Target 2 | Option |
|---|---|---|---|---|---|
| 1 | **BUY**: Failed breakdown | Previous close was **below VAL**, this candle closes **back above VAL** and is green | A dip under the lower blue line that is reclaimed the next day; green `BUY` tag just above VAL | Next two levels above entry: usually an HVN, then the POC | Call, or bull call spread with the short strike near T1 |
| 2 | **BUY**: Bounce off VAL | The low **touches or pierces VAL**, the close is **above VAL**, green candle | A wick into the lower blue line with the body above it | Next two levels above: HVN, then POC | Call / bull call spread |
| 3 | **BUY**: Retest of VAH holds | Previous close was **above VAH**, the low **dips to VAH**, the close **stays above VAH**, green candle | After a breakout over the upper blue line, price pulls back to it and bounces; the `State` row shows "Above value" | Next two levels above; if none, 2×ATR and 4×ATR | Call / bull call spread |
| 4 | **SELL**: Failed breakout | Previous close was **above VAH**, this candle closes **back below VAH** (any color) | A poke over the upper blue line that fails the next day; red `SELL` tag at VAH | Next two levels below: POC or an HVN, then the next HVN | Put, or bear put spread with the short strike near T1 |
| 5 | **SELL**: Rejection at VAH | The high **touches or pierces VAH**, the close is **below VAH**, red candle | An upper wick into the upper blue line with the body below it | Next two levels below: POC / HVN | Put / bear put spread |
| 6 | **SELL**: Retest of VAL fails | Previous close was **below VAL**, the high **rises to VAL**, the close **stays below VAL**, red candle | After a breakdown under the lower blue line, price rallies back to it and is rejected; the `State` row shows "Below value" | Next two levels below; if none, 2×ATR and 4×ATR | Put / bear put spread |
| 7 | **BUY**: Pullback to POC / HVN | `Profile shape` is **P or thin uptrend**; the previous candle was fully above the level, and this candle's low **touches** it | Price drops back onto the yellow POC or an orange HVN line from above; `BUY` tag at the touch | Next two levels above | Call / bull call spread |
| 8 | **SELL**: Pullback to POC / HVN | `Profile shape` is **b or thin downtrend**; the previous candle was fully below the level, and this candle's high **touches** it | Price rallies up into the POC or an HVN from below; `SELL` tag at the touch | Next two levels below | Put / bear put spread |

Signals 3 and 6 are the book's "support becomes resistance" and "reversal trade" setups (pp. 23, 76). Signals 7 and 8 are its volume accumulation and trend setups (pp. 62–69).

### Stops and targets (book pp. 81, 113–118)

| Item | Rule | On the chart |
|---|---|---|
| **Stop** | Behind the volume zone around the signal's level: walk away from the level until a row has less than 50% of an average Value Area row, then add 0.25×ATR. If the signal candle's low/high is further, use that instead. At least 0.5×ATR from the entry | Thick red `Stop (on close)` line below a BUY / above a SELL |
| **Stop rule** | Exit only when a **daily candle closes** beyond the stop. This avoids stop hunts that wick through and reverse | Watch the candle close, not the wick |
| **Hard stop** | 1.5× the stop distance. Exit at once if touched | Dotted red `Hard stop` line |
| **Targets** | The next volume levels, placed 0.1×ATR **in front of** each level | `T1` / `T2` green lines just before an HVN, POC, VAH or VAL |

To go back to the old behavior, set `Stop placement` to "Signal bar" and `Stop rule` to "Touch".

### Filters applied to every signal

| Filter | Default | Effect |
|---|---|---|
| Target distance | 0.5×ATR | A level closer than this to the entry is skipped as a target (e.g. the POC right next to VAH) |
| Min reward:risk to T1 | 1.5 | The signal is hidden when T1 is less than 1.5× as far as the stop. The book prefers about 1:1 (p. 124); 1.5 leaves room for option costs |
| Cooldown | 3 bars | The signal is hidden when the opposite setup fired in the previous 3 bars (price chopping at a line) |

### Matching a live signal on the chart, step by step

1. Wait for the daily candle to **close**. A tag on an unfinished candle can still disappear.
2. Check the tag's location: a `BUY` should sit at the **lower blue line (VAL)** or just above the **upper blue line (VAH)** after a breakout; a `SELL` at the **upper blue line (VAH)** or just under the lower one after a breakdown.
3. Check the `State` and `Profile shape` rows agree with the signal (see the bias and shape tables). A BUY while "Below value", or a SELL while "Above value", goes against the trend: skip it or size down.
4. Read the plan from the table: `Entry`, `Target 1`, `Target 2`, `Stop` (with its hard stop), `R:R to T1`. The same levels are drawn as the green and red zones.
5. Check T1 is at least ~1% from the entry (see "When to skip a trade").
6. Place the trade and write down the exits as **SPY prices**: T1, Stop, and the time stop from the `Time stop` row.
7. Follow it with the `Status (to today)` row: "T1 hit after N bars", "Stopped after N bars", or "Open".

## The main problem: time

An option only pays if SPY reaches the target **before expiration**, and weekly options lose value fast while you wait. Being right about the direction isn't enough; you also have to be right about the timing.

A weekly option gives you **about 5 trading days**. Many signals that eventually reach their target take longer than that, and the option can expire worthless in the meantime.

On SPY anchored 30 Mar 2026 (10 signals), only 1 in 10 reached T1 within 5 bars, but 6 in 10 did within 15. The winners took a median of 12 bars, and 80% of them arrived within 13. Losing trades were stopped within 3–5 bars. These numbers come from a small, optimistic sample, but the pattern is clear: a trade that survives its first week usually needs 2–3 weeks to pay.

So first test whether the signals work on a weekly timescale.

## 1. Check it with the script first

1. Use a **daily** SPY chart, anchored over the last **2–3 months**, with a second copy anchored over 6–12 months for confirmation.
2. **"Max bars per trade (for stats)" is 15** by default, so the History row shows how often T1 was hit **within three weeks**. The `Bars to T1` row shows how long the winners actually took.
3. **"Min reward:risk to Target 1" is 1.5** by default: closer to the book's 1:1 for a higher hit rate, with some room for option costs.
4. Set it to 5 to compare with one week. If T1 is hit often within 15 bars but rarely within 5, the signals are too slow for weekly options; use the longer expiries below.
5. Also try a **4H chart**. Its signals and targets are shorter-term, so they're more likely to resolve within a week.

The history stats are optimistic, because the profile levels are built from the whole range, including bars that came after each signal. Look for a clear margin, not a borderline result.

## 2. How to adapt the trade

| Item | Suggestion | Why |
|---|---|---|
| **Expiration** | At least **1.5× the time stop** in trading days, and never under 2 weeks. With a 13-bar time stop that's about 20+ trading days (4–5 weeks). Count from the day you enter | The trade needs the whole time stop, and time decay speeds up in the last weeks. Very short expiries (0–5 days) are close to gambling on timing |
| **Strike** | At the money or slightly in the money (delta ~0.5–0.7) | Moves closer to one-for-one with SPY, so reaching T1 actually pays |
| **Structure** | Consider a **debit spread**: buy a call near the current price and sell one near **T1** (puts for SELL signals) | Cheaper, less time decay, and the script's T1 is a natural place for the short strike |
| **Entry** | Enter after the signal candle closes | The signal is only valid at the close |
| **Exit on target** | Take profit when SPY reaches **T1**. Hold part of the position for T2 only if there's enough time left | Options decay, so don't wait for perfect |
| **Exit on stop** | Exit when a **SPY daily candle closes** beyond the script's stop, or SPY touches the hard stop. Don't use the option's price | Option prices are noisy, and SPY's price is the actual signal |
| **Time stop** | Exit if SPY hasn't reached T1 by the bar in the `Time stop` row (80% of past T1 hits arrived by then; 13 bars on SPY now) | Past that point most winners would already have paid, and time decay keeps growing |
| **Size** | Risk only **1–2% of your account** per trade, measured by the premium you pay | Full premium can be lost |

## 3. When to skip a trade

- **Major events before expiration:** FOMC meetings, CPI reports and big earnings weeks can cause gaps that skip right past your stop. Option prices also often fall after an event even when you're right.
- **Very high implied volatility:** options are expensive, so you need a bigger move to profit. Prefer a debit spread over buying a single option.
- **SPY sitting at the POC:** the price is in balance, with no edge in either direction.
- **T1 less than about 1% from entry:** the move is too small to overcome option costs.

## 4. A realistic approach

1. Run the script on SPY for a few weeks and write down each signal. Track whether **SPY reached T1 within the time stop**, and what the option you would have chosen would have done.
2. If the numbers hold up, start with **small debit spreads**.
3. Increase size only after 20–30 real trades.

## Pre-trade checklist

- [ ] Daily (or 4H) SPY chart, profiles anchored at 2–3 months and 6–12 months
- [ ] Signal confirmed on a closed candle
- [ ] R:R to T1 is at least 1.5
- [ ] `Profile shape` agrees with the signal's direction (or is D)
- [ ] T1 is at least ~1% from entry
- [ ] No FOMC / CPI / major event before expiration
- [ ] Expiration at least 1.5× the time stop in trading days (never under 2 weeks); strike delta ~0.5–0.7, or a debit spread with the short strike near T1
- [ ] Premium at risk is 1–2% of the account or less
- [ ] Exits written down: T1 (SPY price), stop on daily close (SPY price), hard stop (SPY price), time stop (the `Time stop` row)
