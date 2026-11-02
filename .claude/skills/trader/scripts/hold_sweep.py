"""How many bars do swing_frvp signals take to reach T1? Sweeps holding windows across anchors.

Runs SwingFRVP once per anchor with a long horizon, then follows every past signal bar by bar
(stop checked first, as in SwingFRVP.run) to find when it hit T1, T2 or the stop.

    uv run python .claude/skills/trader/scripts/hold_sweep.py SPY
    uv run python .claude/skills/trader/scripts/hold_sweep.py SPY --anchors 2026-03-30 2026-08-01 --horizon 40
    uv run python .claude/skills/trader/scripts/hold_sweep.py SPY --csv spy.csv
"""
from __future__ import annotations

import argparse
import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))   # repo root, for swing_frvp
from swing_frvp import Config, SwingFRVP, atr_series, load_csv, load_yf  # noqa: E402

WINDOWS = [5, 10, 15, 20, 30]


def follow(eng: SwingFRVP, rb, s, horizon: int):
    """(bars to T1, bars to T2, bars to stop, bars available); None when it didn't happen."""
    t1 = t2 = st = None
    last = min(len(rb) - 1, s.index + horizon)
    for j in range(s.index + 1, last + 1):
        b = rb[j]
        k = j - s.index
        if eng.stop_hit(b, s.stop, s.hard, s.dir):
            st = k
            break
        if t1 is None and ((b.high >= s.t1) if s.dir == 1 else (b.low <= s.t1)):
            t1 = k
        if (b.high >= s.t2) if s.dir == 1 else (b.low <= s.t2):
            t2 = k
            break
    return t1, t2, st, last - s.index


def pct(n: int, d: int) -> str:
    return f"{n}/{d} ({100 * n / d:.0f}%)" if d else "-"


def sweep(ticker: str, anchor: datetime, bars, horizon: int, windows: List[int]) -> Optional[int]:
    cfg = Config(start=anchor, max_hold=horizon)
    eng = SwingFRVP(cfg)
    res = eng.run(bars)
    rb = res.bars
    rows = [(s, *follow(eng, rb, s, horizon)) for s in res.signals]
    print(f"\n{ticker}  anchor {anchor:%Y-%m-%d}  ({len(rb)} bars, {len(rows)} signals, horizon {horizon} bars)")
    if not rows:
        print("  no signals")
        return None

    print(f"  {'window':>7}  {'T1 hit':>12}  {'T2 hit':>12}  {'stopped':>12}  {'neither':>12}  open")
    for w in windows:
        # A signal counts in window w once it resolved (stop or T2) or w bars have passed
        n = t1n = t2n = stn = op = 0
        for s, t1, t2, st, avail in rows:
            done_by = min(x for x in (t2, st, w) if x is not None)
            if avail < done_by:
                op += 1
                continue
            n += 1
            t1n += t1 is not None and t1 <= w
            t2n += t2 is not None and t2 <= w
            stn += st is not None and st <= w and not (t1 is not None and t1 < st)
        print(f"  {w:>4} b  {pct(t1n, n):>12}  {pct(t2n, n):>12}  {pct(stn, n):>12}  {pct(n - t1n - stn, n):>12}  {op}")

    hits = sorted(t1 for _, t1, *_ in rows if t1 is not None)
    if not hits:
        print("  T1 never hit within the horizon")
        return None
    q = statistics.quantiles(hits, n=4) if len(hits) > 1 else [hits[0]] * 3
    need = hits[min(len(hits) - 1, max(0, -(-len(hits) * 8 // 10) - 1))]
    print(f"  bars to T1 (winners only): median {statistics.median(hits):g}, 25-75% {q[0]:g}-{q[2]:g}, max {hits[-1]}")
    print(f"  80% of T1 hits arrive within {need} bars  ->  suggested time stop / holding window")
    if len(rows) < 20:
        print(f"  small sample ({len(rows)} signals): treat these rates as rough")

    s = res.latest
    if s:
        t1, t2, st, _ = rows[-1][1:]
        state = ("T2 hit" if t2 else "stopped" if st else "T1 hit, T2 open" if t1 else "open")
        print(f"  latest: {s.time:%Y-%m-%d} {'BUY' if s.dir == 1 else 'SELL'} {s.why}  entry {s.entry:.2f}  "
              f"T1 {s.t1:.2f}  T2 {s.t2:.2f}  stop {s.stop:.2f}  hard {s.hard:.2f}  -> {state} ({len(rb) - 1 - s.index} bars ago)")
    last_bar(eng, res, bars)
    return need


def last_bar(eng: SwingFRVP, res, bars) -> None:
    """What the last bar did (live, or a setup the filters dropped), and which pullbacks are armed for the next bar."""
    cfg, p, b = eng.cfg, eng.prof, bars[-1]
    raw, why, lvl, _ = eng.sig_at(bars, len(bars) - 1)
    side = {1: "BUY", -1: "SELL"}
    if res.live_dir:
        print(f"  LIVE on last bar: {side[res.live_dir]} {res.live_why}  entry {res.live_entry:.2f}  "
              f"T1 {res.live_t1:.2f}  T2 {res.live_t2:.2f}  stop {res.live_stop:.2f}  R:R {res.live_rr:.1f}")
    elif raw:
        reason = (f"R:R {res.live_rr:.2f} < min {cfg.min_rr}" if cfg.min_rr and res.live_rr < cfg.min_rr
                  else f"opposite signal within {cfg.cooldown} bars (cooldown)")
        print(f"  FILTERED on last bar: {side[raw]} {why} at {lvl:.2f} (entry {res.live_entry:.2f}, T1 {res.live_t1:.2f}, "
              f"stop {res.live_stop:.2f}) - dropped: {reason}. This setup does not fire again on the next bar.")
    else:
        print("  last bar: no setup")

    if p is None or not cfg.use_pull or p.shape_bias == 0:
        print(f"  pullback rules off for a {p.shape_name if p else 'missing'} profile; only Value Area rules (on the close) can fire next")
        return
    d = p.shape_bias
    av = atr_series(bars, cfg.atr_len)[-1]
    # Rules 7/8 need the previous bar fully beyond the level, so only these levels can fire on the next touch
    armed = [x for x in eng.pull_lv if ((b.close > x and b.low > x) if d == 1 else (b.close < x and b.high < x))]
    if not armed:
        print(f"  no {side[d]} pullback armed for the next bar (the last bar was not fully {'above' if d == 1 else 'below'} any POC/HVN)")
    # The rule takes the nearest level price reaches first
    for x in sorted(armed, reverse=(d == 1)):
        t1, t2 = eng.targets_for(x, d, av)
        sl = eng.stop_for(x, x, x, d, av)
        rr = abs(t1 - x) / max(abs(x - sl), cfg.mintick)
        ok = "passes" if not cfg.min_rr or rr >= cfg.min_rr else "FAILS"
        print(f"  armed next bar: {side[d]} pullback on a touch of {x:.2f}  T1 ~{t1:.2f}  T2 ~{t2:.2f}  stop ~{sl:.2f}  "
              f"R:R ~{rr:.2f} ({ok} min {cfg.min_rr}; stop may sit lower if the bar wicks further)")


def main() -> None:
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    ap = argparse.ArgumentParser(description="Bars-to-target sweep for swing_frvp signals")
    ap.add_argument("ticker")
    ap.add_argument("--anchors", nargs="+", help="profile start dates (default: 2026-03-30, ~3 and ~9 months back)")
    ap.add_argument("--horizon", type=int, default=40, help="bars to follow each signal")
    ap.add_argument("--windows", type=int, nargs="+", default=WINDOWS)
    ap.add_argument("--csv")
    ap.add_argument("--interval", default="1d")
    a = ap.parse_args()

    anchors = ([datetime.fromisoformat(x) for x in a.anchors] if a.anchors else
               sorted({Config().start, today - timedelta(days=91), today - timedelta(days=274)}))
    bars = load_csv(a.csv) if a.csv else load_yf(a.ticker, min(anchors), a.interval)
    if bars:
        print(f"Last bar {bars[-1].time:%Y-%m-%d}  close {bars[-1].close:.2f}")
    for anc in anchors:
        sweep(a.ticker, anc, bars, a.horizon, sorted(set(a.windows + [a.horizon])))


if __name__ == "__main__":
    main()
