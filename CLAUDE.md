# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Volume-profile swing-trading signal tools for daily (or intraday) bars, mainly used on SPY to plan weekly/monthly options trades. Two implementations of the same rules:

- `swing_frvp.pine` — TradingView indicator (fixed-range volume profile, Value Area, HVN/LVN, profile shape, BUY/SELL signals, targets, stops, history stats). It is the reference for the rules.
- `swing_frvp.py` — Python port of the Pine script: same rules and defaults, no drawing beyond an optional matplotlib chart. Rules follow the book *Volume Profile: The Insider's Guide to Trading*.

## Keep the Pine script and the Python port in sync

`swing_frvp.pine` and `swing_frvp.py` are one model in two languages. **Every change to either one must be made to the other in the same change**: signal rules, filters, targets, stops, stats, defaults and status-table rows (`report()` rows mirror the Pine table's `putRow` rows, with the same names and wording). Then update what depends on them:

- `HOWTO.md` — chart legend, defaults, timing and options guidance.
- `.claude/skills/trader/` — `SKILL.md` names the report rows it reads, and `scripts/hold_sweep.py` imports `Config`, `SwingFRVP`, `Result` fields and `t1_within` from `swing_frvp.py`.

The Pine script can't be compiled here; after editing it, say so and ask the user to paste it into TradingView's Pine Editor and compare its table with `uv run python swing_frvp.py SPY`.

`HOWTO.md` (also the package readme) is the user-facing trading guide: chart legend, the 8 signal rules, stop/target placement, filters, and how to turn signals into SPY options trades. Keep it in sync when signal rules or defaults change.

## Commands

Dependencies are managed with `uv` (Python ≥3.9; the local `.venv` is 3.9, so keep `from __future__ import annotations` and `typing.List`-style hints). Data comes from Yahoo via `yfinance` (unadjusted bars) unless `--csv` is given.

```bash
uv run python swing_frvp.py SPY --start 2026-03-30          # default start matches the Pine script
uv run python swing_frvp.py SPY --start 2026-03-30 --save spy.png
uv run python swing_frvp.py SPY --csv spy.csv --max-hold 15 # CSV: date/datetime/time/timestamp + OHLCV columns
uv run python swing_frvp.py --help
```

The console script `swing-frvp` is also defined in `pyproject.toml`. There are no tests, linter or CI; verify changes by running the script on SPY and comparing its report against the Pine indicator on TradingView.

## Architecture

`swing_frvp.py` pipeline: `Config` dataclass (CLI flags map onto it) → `load_yf` / `load_csv` → `SwingFRVP.run(bars)` → `Result` → `report()` text table, optional `plot()`.

- `run()` receives the whole chart including warm-up history before the start date (needed for ATR and the live signal); it filters to the range bars, builds the profile once from the **entire range**, then walks bars newest-first to find signals. Each signal gets T1/T2 (`targets_for`), a volume-zone stop (`stop_for`) and hard stop (`hard_mult` × stop distance), is filtered by `min_rr` and the opposite-signal cooldown (`choppy`), and is scored over `max_hold` bars into `Stats` (T1 / T2 / stopped / expired / open). `follow()` then tracks the latest signal to today, and `live()` evaluates the unclosed last bar.
- Because the profile uses bars after each signal, the history stats have look-ahead bias; HOWTO.md calls them optimistic. Keep that in mind when interpreting or "improving" backtest numbers.
- Signal order (first match wins): Value Area rules in `eval_sig` (failed breakdown/breakout, bounce/rejection, retests), then pullback-to-POC/HVN rules in `eval_pull`, gated by profile shape (P / b / thin).
- Stop rule `close` (default) exits on a daily close beyond the stop or a touch of the hard stop; `touch` is the old behavior.
- Timing: each signal is also followed for `t1_horizon` bars (`bars_to_t1`) into `Result.t1_bars`. The `Bars to T1` row shows the median and the 80% figure (`t1_within`), and the `Time stop` row applies that figure to the latest signal.
- `live()` also records a setup the filters dropped on the last bar (`live_raw`, `live_chop` → `Filtered (last bar)`) and the pullback levels the next bar can fire on (`Result.armed` → `Armed next bar`). Rules 7/8 need the previous bar fully beyond the level, so a level the last bar wicked into is not armed.
