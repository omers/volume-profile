---
name: trader
description: Turns this repo's volume-profile model (swing_frvp.py / swing_frvp.pine) into a concrete trade plan for a ticker, usually SPY - runs the model, reads TradingView volume-profile screenshots (VAH, VAL, POC, HVN, LVN, hard stop), measures how many bars signals take to reach their targets, and turns a BUY/SELL signal into an SSO (BUY) or SDS (SELL) trade with the ETF-translated exits, position size and time stop. Use it whenever the user asks how to trade a ticker today, what the model or indicator says, what a chart screenshot means, which ETF (SSO, SDS), size or holding period to use for a BUY/SELL signal, or how many bars/trading days until a date - even if they don't mention the script or the word "skill". Also keeps the user's trade journal (trades.csv): use it whenever they say they opened, bought, entered, closed, sold or exited a position (real or backtest/paper), or ask about their trades, open positions or P&L.
---

# Trader

Produce a trade plan from the repo's own model and rules, so the answer is grounded in numbers the user can reproduce rather than in an opinion about the market.

The user is researching their own system. Present everything as **what the model and HOWTO.md rules produce**, with the evidence, and let them decide. Don't tell them to buy or sell, and don't size positions to their account beyond quoting HOWTO's 1-2% rule. One closing line saying it's the model's output, not personal financial advice, is enough; don't lecture.

## Sources of truth

- `swing_frvp.py` — the model (Python port of `swing_frvp.pine`; same rules and defaults).
- `HOWTO.md` — signal rules, stop/target placement, the SSO/SDS trade mapping and sizing, skip rules, pre-trade checklist. Read the "How to adapt the trade" and "When to skip a trade" sections before writing a trade plan; they may have changed since this skill was written.
- `scripts/hold_sweep.py` (this skill) — bars-to-target statistics across anchors.
- `scripts/trading_days.py` (this skill) — NYSE trading-day counts (used for the time stop date; its `expiries` command is no longer needed).
- `scripts/journal.py` (this skill) — reads and writes the trade journal `trades.csv` at the repo root (see "Trade journal").

Run everything from the repo root with `uv run python ...` (the venv has yfinance and matplotlib). Ignore the urllib3 `NotOpenSSLWarning`.

## Workflow

### 1. Establish the inputs

- **Ticker**: default SPY. The model and all levels are on SPY; the trades are placed in SSO (BUY) or SDS (SELL), per HOWTO. If the user gave a screenshot without a ticker, say you're assuming SPY. If the user names another instrument (UPRO, SPXL, options), say HOWTO's instruments are SSO/SDS and answer for what they asked, translating the levels the same way (see step 5).
- **Anchor**: the model's default start is 2026-03-30. If a screenshot or the user implies another start, use it.
- **Today and the last bar**: Yahoo's last daily bar is the last close. On a weekend or before the open, "today's" plan is based on the previous close - say which date the numbers are from.

### 2. Run the model

```bash
uv run python swing_frvp.py SPY --start 2026-03-30
uv run python .claude/skills/trader/scripts/hold_sweep.py SPY
```

From `swing_frvp.py` take: `State`, `Profile shape`, VAH/POC/VAL, HVNs/LVNs with distances, `Latest signal` with entry/T1/T2/stop/hard stop and `Status (to today)`, `History`, `Bars to T1`, `Time stop`, `Live (last bar)`, `Filtered (last bar)` and `Armed next bar`. The Pine indicator's table shows the same rows, so they can be matched against a screenshot.

From `hold_sweep.py` take, per anchor: the T1 hit rate by window, the median bars to T1, the "80% of T1 hits arrive within N bars" figure, and the sample size. It also sweeps ~3- and ~9-month anchors, matching HOWTO's advice to confirm with a second anchor. Where anchors disagree, say so; don't average them away.

Its last-bar lines (the same data as the model's `Filtered (last bar)` and `Armed next bar` rows, with T2 and the failing levels too) are what "what would trigger next" must be built on:

- `FILTERED on last bar` — a setup that matched the rules on the last bar but was dropped (R:R below the minimum, or cooldown). Report it, with the reason. It has already been used up: don't present a touch of that level as a pending trigger.
- `armed next bar` — pullback levels (rules 7/8) that can fire on the next bar's first touch, nearest first, with approximate T1/T2/stop and whether R:R passes. A level that isn't listed can't fire a pullback signal next bar, because the rule needs the previous bar fully beyond it. A level marked FAILS wouldn't show a signal either.

Don't work out triggers by eye from the levels table; price that already wicked into a node changes what can fire.

### 3. Reading a screenshot (when the user sends one)

Read the labels first: VAH, VAL, POC, HVN, LVN, `Hard stop`, `T1`/`T2`, `Stop (on close)`, the BUY/SELL tag. To estimate the current price, interpolate the last candle's close linearly between two labelled levels using pixel positions (price = upper + (lower - upper) x (y - y_upper) / (y_lower - y_upper)), and say it's an estimate.

Then describe: where price sits relative to value (above VAH / inside / below VAL), which node it's on or between, where the thin (low-volume) stretches are that price could move through quickly, and what a visible hard stop implies (an open trade from the indicator). Always follow with step 2: a screenshot alone can't give the signal history or timing, and the script's levels can differ slightly from the chart's (different row counts or data). Prefer the script's numbers and mention material differences.

### 4. Pick the holding window ("which bars")

The holding window is how long a trade is given to reach T1 before the time stop. Base it on the sweep (or the model's `Time stop` row, which uses the same 80% figure):

- Use the "80% of T1 hits arrive within N bars" figure from the anchor the signal came from (normally 2026-03-30) as the time stop.
- If the T1 rate keeps rising well past 5 bars (e.g. 10% at 5 bars, 56% at 15), say plainly that a one-week time stop would have cut most of the winners, and that a long hold means more daily-reset drag on the ETF.
- With fewer than ~20 signals, call the numbers rough and give the range, not one precise number.

### 5. Pick the ETF trade ("which instrument and size")

Only build a trade plan for a signal that is **live on the last bar** or **still open** (not stopped, T2 not hit). If there's none, say so and describe what would trigger the next one: the `armed next bar` levels that pass R:R (pullback rules 7/8, on the first touch) and the Value Area rules that can fire on a close at VAL/VAH (rules 1-6), each with the ETF it would use. Don't invent an entry.

For a live or open signal:

- **Instrument**: BUY -> buy **SSO** (2x S&P 500). SELL -> buy **SDS** (-2x S&P 500). Shares only; always long the ETF, never short SSO or SDS. There is no expiry, strike or spread.
- **Exits, in SPY prices** (decisions are made on SPY, not on the ETF's price): T1 (take profit), T2 (runner, only if time remains), stop on a **daily close** beyond `Stop`, immediate exit at `Hard stop` (tell the user to place it as a real order, since a gap past a close-based stop loses 2x the SPY gap), and the time stop date. Get the date with

  ```bash
  python3 .claude/skills/trader/scripts/trading_days.py add <holding bars> --from <entry date>
  ```

  For an already-open signal, count the bars left from today.
- **ETF translation**: the ETF moves about 2x SPY's percent move in the trade's direction, so show T1, T2, stop and hard stop as SPY % from entry and as approximate ETF % (2x). Add approximate ETF prices only if you have fetched the ETF's last close (yfinance `SSO` / `SDS`); label them approximate, since the daily reset makes the path matter.
- **Size**: HOWTO's formula, position = account x risk % / (2 x stop distance in %), for 1-2% risk. Without an account size, give it as a share of the account (e.g. a 0.8% SPY stop is about -1.6% on the ETF, so 1% risk is about 60% of the account). Don't ask for or assume the account size.
- **Skip checks** (HOWTO "When to skip a trade"): T1 less than ~1% from entry; price sitting at the POC; price chopping between VAL and VAH with no progress; FOMC, CPI or major earnings before the time stop. You can't see the economic calendar, so name the likely events in the window if you know them and tell the user to confirm the dates.

### 6. Answer

Use this structure. Keep it tight, using tables for levels and timing.

```markdown
## <TICKER>: model read as of <last bar date> close <price>

**Model state**: <state, shape, where price sits vs. VAH/POC/VAL and the nearest nodes>
**Signal**: <live / open / none - with entry, T1, T2, stop, hard stop, status>

### Timing (which bars)
<table: window | T1 hit | stopped, for the signal's anchor>
<median bars to T1, 80% figure -> time stop date; second-anchor check; sample-size caveat>

### ETF plan (SSO / SDS)
<only for a live/open signal: which ETF, a table of T1 / T2 / stop / hard stop as SPY price, SPY % and approximate ETF %, time stop date, position size as a share of the account at 1-2% risk>
<or: no signal - what would trigger the next one, and which ETF it would use>

### Before acting
<skip checks that apply, events to verify, what to re-check at the next close>

_Model output for research, not personal financial advice._
```

## Trade journal (trades.csv)

The user logs their positions in `trades.csv` at the repo root, one row per position:

```
open_date,symbol,amount,buy_price,real,sell_date,close_price
```

- `amount` is the number of shares. `real` is `true` for a real trade and `false` for a backtest or paper record. A row is open while `sell_date` and `close_price` are empty. Dates are `YYYY-MM-DD`.
- Always go through `scripts/journal.py`; don't edit the CSV by hand. It validates the values, writes atomically and refuses ambiguous closes.

```bash
python3 .claude/skills/trader/scripts/journal.py open <SYMBOL> <shares> <price> --real true|false [--date YYYY-MM-DD]
python3 .claude/skills/trader/scripts/journal.py close <SYMBOL> <price> [--date YYYY-MM-DD] [--amount N] [--real true|false] [--row N]
python3 .claude/skills/trader/scripts/journal.py list [--real true|false]
```

**When the user says they opened a position** ("I bought 78 SSO at 63.85"): record it with `open`.

- `real`: if they say backtest, paper, simulated or "not real", use `false`; if they say they actually bought it, use `true`. If it isn't clear, ask; don't guess, because mixing the two corrupts both records.
- Date: the date they give, else today. If they say "yesterday" or "Monday", convert it to the actual date and say which date you used.
- If they give a dollar amount instead of shares ("$5000 of SSO at 63.85"), compute whole shares (floor of amount / price), say the number, and record shares.
- For a backtest batch ("open and close these trades"), run `open` then `close` per trade with `--real false` and the historical dates.

**When the user says they closed a position** ("sold my SSO at 66.30"): record it with `close`.

- The script matches the one open row for that symbol. If it reports several open rows, show them and ask which one (then pass `--row N`), or pass `--real` when that alone disambiguates.
- For a partial close ("sold 40 of my 78"), pass `--amount 40`: the script closes 40 on that row and keeps the rest open in a new row right after it.

**After every change**, show the row the script printed (with the P&L for a close). For a real position that matches the model's latest signal, add the plan's exits (T1, stop, hard stop, time stop date) in one or two lines, so the user knows what to watch. Don't re-run the full analysis unless asked.

**When asked about trades, positions or P&L**, run `list` (with `--real` to separate real from backtest) and summarize. Keep real and backtest results apart; never add them together.

## Pitfalls

- The model's history stats have look-ahead bias (the profile uses bars after each signal). Call them optimistic when hit rates matter to the decision.
- Yahoo data can lag or revise; if the last bar isn't the expected date, say so.
- `hold_sweep.py` counts a trade that hit T1 and was later stopped as a T1 hit, as the model's stats do.
- SSO and SDS reset daily: over choppy days they lose value even if SPY ends flat, and gaps cost 2x. Don't quote ETF targets as exact; the SPY levels are the real exits.
- The journal's prices are ETF prices (SSO/SDS), while the model's levels are SPY prices. Don't compare a journal `buy_price` with T1 or the stop directly; translate first (step 5).
