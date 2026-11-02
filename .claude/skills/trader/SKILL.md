---
name: trader
description: Turns this repo's volume-profile model (swing_frvp.py / swing_frvp.pine) into a concrete trade plan for a ticker, usually SPY - runs the model, reads TradingView volume-profile screenshots (VAH, VAL, POC, HVN, LVN, hard stop), measures how many bars signals take to reach their targets, and picks the option expiry, strike or spread and time stop that fit. Use it whenever the user asks how to trade a ticker today, what the model or indicator says, what a chart screenshot means, which option, expiry, strike, DTE or holding period to use for a BUY/SELL signal, or how many bars/trading days until a date - even if they don't mention the script or the word "skill".
---

# Trader

Produce a trade plan from the repo's own model and rules, so the answer is grounded in numbers the user can reproduce rather than in an opinion about the market.

The user is researching their own system. Present everything as **what the model and HOWTO.md rules produce**, with the evidence, and let them decide. Don't tell them to buy or sell, and don't size positions to their account beyond quoting HOWTO's 1-2% rule. One closing line saying it's the model's output, not personal financial advice, is enough; don't lecture.

## Sources of truth

- `swing_frvp.py` — the model (Python port of `swing_frvp.pine`; same rules and defaults).
- `HOWTO.md` — signal rules, stop/target placement, options guidance, skip rules, pre-trade checklist. Read the "How to adapt the trade" and "When to skip a trade" sections before writing an options plan; they may have changed since this skill was written.
- `scripts/hold_sweep.py` (this skill) — bars-to-target statistics across anchors.
- `scripts/trading_days.py` (this skill) — NYSE trading-day counts and expiry dates.

Run everything from the repo root with `uv run python ...` (the venv has yfinance and matplotlib). Ignore the urllib3 `NotOpenSSLWarning`.

## Workflow

### 1. Establish the inputs

- **Ticker**: default SPY (HOWTO is written for SPY options). If the user gave a screenshot without a ticker, say you're assuming SPY.
- **Anchor**: the model's default start is 2026-03-30. If a screenshot or the user implies another start, use it.
- **Today and the last bar**: Yahoo's last daily bar is the last close. On a weekend or before the open, "today's" plan is based on the previous close - say which date the numbers are from.

### 2. Run the model

```bash
uv run python swing_frvp.py SPY --start 2026-03-30
uv run python .claude/skills/trader/scripts/hold_sweep.py SPY
```

From `swing_frvp.py` take: `State`, `Profile shape`, VAH/POC/VAL, HVNs/LVNs with distances, `Latest signal` with entry/T1/T2/stop/hard stop and `Status (to today)`, `Live (last bar)`, and the `History` line.

From `hold_sweep.py` take, per anchor: the T1 hit rate by window, the median bars to T1, the "80% of T1 hits arrive within N bars" figure, and the sample size. It also sweeps ~3- and ~9-month anchors, matching HOWTO's advice to confirm with a second anchor. Where anchors disagree, say so; don't average them away.

Also take its last-bar lines, which are what "what would trigger next" must be built on:

- `FILTERED on last bar` — a setup that matched the rules on the last bar but was dropped (R:R below the minimum, or cooldown). Report it, with the reason. It has already been used up: don't present a touch of that level as a pending trigger.
- `armed next bar` — pullback levels (rules 7/8) that can fire on the next bar's first touch, nearest first, with approximate T1/T2/stop and whether R:R passes. A level that isn't listed can't fire a pullback signal next bar, because the rule needs the previous bar fully beyond it. A level marked FAILS wouldn't show a signal either.

Don't work out triggers by eye from the levels table; price that already wicked into a node changes what can fire.

### 3. Reading a screenshot (when the user sends one)

Read the labels first: VAH, VAL, POC, HVN, LVN, `Hard stop`, `T1`/`T2`, `Stop (on close)`, the BUY/SELL tag. To estimate the current price, interpolate the last candle's close linearly between two labelled levels using pixel positions (price = upper + (lower - upper) x (y - y_upper) / (y_lower - y_upper)), and say it's an estimate.

Then describe: where price sits relative to value (above VAH / inside / below VAL), which node it's on or between, where the thin (low-volume) stretches are that price could move through quickly, and what a visible hard stop implies (an open trade from the indicator). Always follow with step 2: a screenshot alone can't give the signal history or timing, and the script's levels can differ slightly from the chart's (different row counts or data). Prefer the script's numbers and mention material differences.

### 4. Pick the holding window ("which bars")

The holding window is how long a trade is given to reach T1 before the time stop. Base it on the sweep, not on HOWTO's default 3-5 days:

- Use the "80% of T1 hits arrive within N bars" figure from the anchor the signal came from (normally 2026-03-30) as the time stop.
- If the T1 rate keeps rising well past 5 bars (e.g. 10% at 5 bars, 56% at 15), say plainly that HOWTO's 3-5 day time stop would have cut most of the winners.
- With fewer than ~20 signals, call the numbers rough and give the range, not one precise number.

### 5. Pick the option ("which options")

Only build an option plan for a signal that is **live on the last bar** or **still open** (not stopped, T2 not hit). If there's none, say so and describe what would trigger the next one: the `armed next bar` levels that pass R:R (pullback rules 7/8, on the first touch) and the Value Area rules that can fire on a close at VAL/VAH (rules 1-6), each with the option template it would use. Don't invent an entry.

For a live or open signal:

- **Type**: BUY -> calls or a bull call spread; SELL -> puts or a bear put spread.
- **Expiry**: the time stop must fall well before expiry, because time decay accelerates in the last weeks. Run

  ```bash
  python3 .claude/skills/trader/scripts/trading_days.py add <holding bars> --from <entry date>
  python3 .claude/skills/trader/scripts/trading_days.py expiries --from <today> --weeks 10
  ```

  and pick an expiry with at least ~1.5x the holding window in trading days, and never fewer than HOWTO's 2-4 weeks. Give two candidates (usually a weekly and the next monthly) with their trading-day counts. For an already-open signal, count from today, not from the original entry.
- **Strike**: at the money to slightly in the money (delta ~0.5-0.7), or a debit spread with the long strike near the current price and the short strike near **T1**. Express strikes relative to price and T1 (e.g. "long ~755, short ~751 put spread") - you don't have an options chain, so don't quote premiums or exact deltas.
- **Exits, in underlying prices**: T1 (take profit), T2 (runner, only if time remains), stop on a **daily close** beyond `Stop`, immediate exit at `Hard stop`, and the time stop date from step 4.
- **Skip checks** (HOWTO "When to skip a trade"): T1 less than ~1% from entry; price sitting at the POC; FOMC, CPI or major earnings before expiry; very high implied volatility. You can't see the economic calendar or IV, so name the likely events in the window if you know them and tell the user to confirm the dates.

### 6. Answer

Use this structure. Keep it tight, using tables for levels and timing.

```markdown
## <TICKER>: model read as of <last bar date> close <price>

**Model state**: <state, shape, where price sits vs. VAH/POC/VAL and the nearest nodes>
**Signal**: <live / open / none - with entry, T1, T2, stop, hard stop, status>

### Timing (which bars)
<table: window | T1 hit | stopped, for the signal's anchor>
<median bars to T1, 80% figure -> time stop date; second-anchor check; sample-size caveat>

### Option plan (which options)
<only for a live/open signal: type, two expiry candidates with trading-day counts, strike or spread vs. T1, exits as underlying prices, time stop date>
<or: no signal - what would trigger the next one, and the template it would use>

### Before acting
<skip checks that apply, events to verify, what to re-check at the next close>

_Model output for research, not personal financial advice._
```

## Pitfalls

- The model's history stats have look-ahead bias (the profile uses bars after each signal). Call them optimistic when hit rates matter to the decision.
- Yahoo data can lag or revise; if the last bar isn't the expected date, say so.
- `hold_sweep.py` counts a trade that hit T1 and was later stopped as a T1 hit, as the model's stats do.
