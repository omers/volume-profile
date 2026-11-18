"""Backtest stop variants on swing_frvp signals, with HOWTO's exits.

Per signal: entry at the signal close; half sold at T1, the rest at T2, the stop, the hard stop
or the time stop. Within a bar, stops are checked before targets (conservative), and a gap past
a stop fills at the open. Signals are scored independently (no overlapping-signal rule).
Returns are SPY %; SSO/SDS move about 2x. The model's profile uses bars after each signal,
so the numbers are optimistic (look-ahead bias).

Variants: the model's close stop; a touch stop at the same level (a resting stop order); the
close stop moved 1.25x / 1.5x / 2x farther (hard stop scaled with it); hard stop only; and two
model settings (stop_buf, min_stop_atr) that also change which signals pass the R:R filter.

    uv run python .claude/skills/trader/scripts/stop_sweep.py SPY
    uv run python .claude/skills/trader/scripts/stop_sweep.py SPY --anchors 2026-03-30 --detail
    uv run python .claude/skills/trader/scripts/stop_sweep.py SPY --csv spy.csv --time-stop 16
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))   # repo root, for swing_frvp
from swing_frvp import CACHE_DIR, Config, SwingFRVP, load_csv, load_yf  # noqa: E402

# (name, Config overrides, stop distance multiplier, stop rule, hard stop only)
VARIANTS = [
    ("close x1.0 (model)", {}, 1.0, "close", False),
    ("touch x1.0 (stop order)", {}, 1.0, "touch", False),
    ("close x1.25", {}, 1.25, "close", False),
    ("close x1.5", {}, 1.5, "close", False),
    ("close x2.0", {}, 2.0, "close", False),
    ("hard stop only", {}, 1.0, "close", True),
    ("model stop_buf 0.5", {"stop_buf": 0.5}, 1.0, "close", False),
    ("model min_stop_atr 1.0", {"min_stop_atr": 1.0}, 1.0, "close", False),
]


def sim(rb, s, sl: float, hard: float, rule: str, k: int) -> Tuple[float, str]:
    """SPY return (fraction) and how the trade ended."""
    d, e = s.dir, s.entry
    left, pnl, t1hit = 1.0, 0.0, False
    for j in range(s.index + 1, min(len(rb), s.index + k + 1)):
        b = rb[j]
        lo, hi = (b.low, b.high) if d == 1 else (-b.high, -b.low)
        op, cl = d * b.open, d * b.close
        H, S, T1, T2 = d * hard, d * sl, d * s.t1, d * s.t2
        x = None
        if op <= H or (rule == "touch" and op <= S):
            x = op
        elif lo <= H:
            x = H
        elif rule == "touch" and lo <= S:
            x = S
        if x is not None:
            return pnl + left * (x - d * e) / e, "T1+stop" if t1hit else "stop"
        if not t1hit and hi >= T1:
            t1hit = True
            pnl += 0.5 * (T1 - d * e) / e
            left = 0.5
        if hi >= T2:
            return pnl + left * (T2 - d * e) / e, "T2"
        if rule == "close" and cl < S:
            return pnl + left * (cl - d * e) / e, "T1+stop" if t1hit else "stop"
        if j == s.index + k:
            return pnl + left * (cl - d * e) / e, "T1+time" if t1hit else "time"
    return pnl + left * (d * rb[-1].close - d * e) / e, "open"


def run(bars, anchor: datetime, kw: dict, mult: float, rule: str, hard_only: bool, k: int):
    cfg = Config(start=anchor, **kw)
    res = SwingFRVP(cfg).run(bars)
    out = []
    for s in res.signals:
        dist = s.entry - s.stop
        sl = s.entry - dist * mult
        hard = s.entry - dist * mult * cfg.hard_mult
        r, how = sim(res.bars, s, hard if hard_only else sl, hard, rule, k)
        out.append((s, r, how))
    return out


def main(argv: Optional[List[str]] = None) -> None:
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    ap = argparse.ArgumentParser(description="Stop-variant backtest for swing_frvp signals")
    ap.add_argument("ticker")
    ap.add_argument("--anchors", nargs="+", help="profile start dates (default: 2026-03-30, ~3, ~9 and ~12 months back)")
    ap.add_argument("--time-stop", type=int, default=16, help="bars before an unfinished trade is closed")
    ap.add_argument("--detail", action="store_true", help="list every trade per variant")
    ap.add_argument("--include-today", action="store_true", help="keep a bar dated today (it may still be forming)")
    ap.add_argument("--csv")
    ap.add_argument("--refresh", action="store_true", help="ignore the Yahoo cache and download everything again")
    a = ap.parse_args(argv)

    anchors = ([datetime.fromisoformat(x) for x in a.anchors] if a.anchors else
               sorted({Config().start} | {today - timedelta(days=n) for n in (91, 274, 366)}))
    bars = load_csv(a.csv) if a.csv else load_yf(a.ticker, min(anchors), cache_dir=CACHE_DIR, refresh=a.refresh)
    if not a.include_today:
        bars = [b for b in bars if b.time < today]
    if not bars:
        sys.exit("no bars")
    print(f"Last bar {bars[-1].time:%Y-%m-%d}  close {bars[-1].close:.2f}  (time stop {a.time_stop} bars, SPY %; ETF ~2x)")

    for anc in anchors:
        print(f"\n=== anchor {anc:%Y-%m-%d} ===")
        print(f"{'variant':26} {'n':>3} {'sum%':>7} {'avg%':>6} {'win':>4} {'loss':>4} {'worst%':>7} {'stops':>5} {'open':>4}")
        for name, kw, m, rule, ho in VARIANTS:
            out = run(bars, anc, kw, m, rule, ho, a.time_stop)
            closed = [(s, r, h) for s, r, h in out if h != "open"]
            rs = [r * 100 for _, r, _ in closed]
            n_open = len(out) - len(closed)
            if not rs:
                print(f"{name:26} no closed trades ({n_open} open)")
                continue
            stops = sum(1 for _, _, h in closed if h.endswith("stop"))
            print(f"{name:26} {len(rs):3} {sum(rs):7.2f} {sum(rs) / len(rs):6.2f} {sum(r > 0 for r in rs):4} "
                  f"{sum(r <= 0 for r in rs):4} {min(rs):7.2f} {stops:5} {n_open:4}")
            if a.detail:
                for s, r, h in out:
                    print(f"     {s.time:%Y-%m-%d} {'BUY ' if s.dir == 1 else 'SELL'} {r * 100:6.2f}% {h}")


if __name__ == "__main__":
    main()
