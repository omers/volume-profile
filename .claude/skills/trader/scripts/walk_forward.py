"""Point-in-time (walk-forward) backtest of swing_frvp: what would actually have traded.

The model's History row and the hold/stop sweeps score signals on a profile built from the whole
range, including bars after each signal, so the signal list itself changes as new bars arrive
(on 2026-11-20 the 2026-11-06 BUY dropped out of it). Here, for every bar after the anchor, the
model is run on the bars up to that close only, and the live signal on that bar is taken, as the
executor would. Exits follow HOWTO via stop_sweep.sim: half at T1, the rest at T2, the stop on a
close, the hard stop or the time stop. Signals are scored independently (no overlapping-signal
rule, no late entry). Returns are SPY %; SSO/SDS move about 2x.

Variants (subclasses or Config overrides, nothing in swing_frvp.py changes):
  model                       the current rules and defaults
  cooldown 0                  no opposite-signal cooldown
  cooldown: R:R-passing only  only an opposite setup that passed min_rr starts a cooldown
  no pullback against VA      BUY pullbacks need the previous close >= VAL, SELL pullbacks <= VAH
  retest stop at level        rules 3/6 (retest of VAH holds / VAL fails): stop just beyond the level
                              and the signal bar, not walked through the Value Area
  retest stop + R:R cooldown  the two above together
The two retest variants came from the 2026-11-16 week (post-hoc); judge them on later signals.

    uv run python .claude/skills/trader/scripts/walk_forward.py SPY
    uv run python .claude/skills/trader/scripts/walk_forward.py SPY --anchors 2026-03-30 --detail
"""
from __future__ import annotations

import argparse
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))   # repo root, for swing_frvp
sys.path.insert(0, str(Path(__file__).resolve().parent))      # stop_sweep.sim
from swing_frvp import CACHE_DIR, Config, SwingFRVP, atr_series, load_csv, load_yf, side_etf  # noqa: E402
from stop_sweep import sim  # noqa: E402


class RRCooldown(SwingFRVP):
    """Only an opposite setup that would have passed min_rr counts toward the cooldown."""

    def choppy(self, bars, i, d, first=1):
        atr = atr_series(bars, self.cfg.atr_len)
        for k in range(1, self.cfg.cooldown + 1):
            j = i - k
            if j < first:
                continue
            dd, _, lvl, ent = self.sig_at(bars, j)
            if dd != -d:
                continue
            e = bars[j].close if math.isnan(ent) else ent
            t1, _ = self.targets_for(e, dd, atr[j])
            sl = self.stop_for(e, e if math.isnan(lvl) else lvl, bars[j].low if dd == 1 else bars[j].high, dd, atr[j])
            if self.cfg.min_rr == 0 or abs(t1 - e) / max(abs(e - sl), self.cfg.mintick) >= self.cfg.min_rr:
                return True
        return False


class NoCounterPull(SwingFRVP):
    """No pullback trade from the wrong side of the Value Area."""

    def eval_pull(self, o, h, l, pc, ph, pl):
        r = super().eval_pull(o, h, l, pc, ph, pl)
        p = self.prof
        if (r[0] == 1 and pc < p.val) or (r[0] == -1 and pc > p.vah):
            return 0, "", math.nan, math.nan
        return r


def _retest_stop(self, ent, lvl, ext, d, atr_v):
    p = self.prof
    if p is not None and ((d == -1 and abs(lvl - p.val) < 1e-9) or (d == 1 and abs(lvl - p.vah) < 1e-9)):
        base = max(lvl, ext) if d == -1 else min(lvl, ext)
        sl = base - d * self.cfg.stop_buf * atr_v
        if (ent - sl) * d < self.cfg.min_stop_atr * atr_v:
            sl = ent - d * self.cfg.min_stop_atr * atr_v
        return sl
    return SwingFRVP.stop_for(self, ent, lvl, ext, d, atr_v)


class RetestStop(SwingFRVP):
    stop_for = _retest_stop


class RetestStopRR(RRCooldown):
    stop_for = _retest_stop


VARIANTS = [
    ("model", SwingFRVP, {}),
    ("cooldown 0", SwingFRVP, {"cooldown": 0}),
    ("cooldown: R:R-passing only", RRCooldown, {}),
    ("no pullback against VA", NoCounterPull, {}),
    ("retest stop at level", RetestStop, {}),
    ("retest stop + R:R cooldown", RetestStopRR, {}),
]


def walk(bars, anchor: datetime, cls, kw: dict, k: int, warm: int):
    """Live signals bar by bar, each scored with HOWTO's exits over k bars."""
    cfg = Config(start=anchor, **kw)
    idx = [i for i, b in enumerate(bars) if b.time >= anchor]
    out = []
    for i in idx[warm:]:
        r = cls(cfg).run(bars[: i + 1])
        if not r.live_dir:
            continue
        d, e, sl = r.live_dir, r.live_entry, r.live_stop
        hard = e + (sl - e) * cfg.hard_mult
        s = SimpleNamespace(dir=d, entry=e, t1=r.live_t1, t2=r.live_t2, index=i)
        ret, how = sim(bars, s, sl, hard, "close", k)
        out.append((bars[i].time, d, r.live_why, e, ret, how))
    return out


def main(argv: Optional[List[str]] = None) -> None:
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    ap = argparse.ArgumentParser(description="Point-in-time (walk-forward) backtest of swing_frvp")
    ap.add_argument("ticker")
    ap.add_argument("--anchors", nargs="+", help="profile start dates (default: 2026-03-30, ~3, ~9 and ~12 months back)")
    ap.add_argument("--time-stop", type=int, default=16, help="bars before an unfinished trade is closed")
    ap.add_argument("--warmup", type=int, default=20, help="bars after the anchor before signals count (the profile needs some)")
    ap.add_argument("--variants", nargs="+", help="variant names to run (default: all)")
    ap.add_argument("--detail", action="store_true", help="list every trade per variant")
    ap.add_argument("--include-today", action="store_true", help="keep a bar dated today (it may still be forming)")
    ap.add_argument("--csv")
    ap.add_argument("--refresh", action="store_true", help="ignore the Yahoo cache and download everything again")
    a = ap.parse_args(argv)

    anchors = ([datetime.fromisoformat(x) for x in a.anchors] if a.anchors else
               sorted({Config().start} | {today - timedelta(days=n) for n in (91, 274, 366)}))
    variants = [v for v in VARIANTS if not a.variants or v[0] in a.variants]
    if not variants:
        sys.exit("no such variant; choose from: " + ", ".join(repr(v[0]) for v in VARIANTS))
    bars = load_csv(a.csv) if a.csv else load_yf(a.ticker, min(anchors), cache_dir=CACHE_DIR, refresh=a.refresh)
    if not a.include_today:
        bars = [b for b in bars if b.time < today]
    if not bars:
        sys.exit("no bars")
    print(f"Last bar {bars[-1].time:%Y-%m-%d}  close {bars[-1].close:.2f}  "
          f"(point-in-time; time stop {a.time_stop} bars, SPY %; ETF ~2x)")

    for anc in anchors:
        print(f"\n=== anchor {anc:%Y-%m-%d} ===")
        print(f"{'variant':28} {'n':>3} {'sum%':>7} {'avg%':>6} {'win':>4} {'loss':>4} {'best%':>6} {'worst%':>7} {'open':>4}")
        for name, cls, kw in variants:
            out = walk(bars, anc, cls, kw, a.time_stop, a.warmup)
            closed = [t for t in out if t[5] != "open"]
            rs = [t[4] * 100 for t in closed]
            n_open = len(out) - len(closed)
            if not rs:
                print(f"{name:28} no closed trades ({n_open} open)")
            else:
                print(f"{name:28} {len(rs):3} {sum(rs):7.2f} {sum(rs) / len(rs):6.2f} {sum(r > 0 for r in rs):4} "
                      f"{sum(r <= 0 for r in rs):4} {max(rs):6.2f} {min(rs):7.2f} {n_open:4}")
            if a.detail:
                for tm, d, why, e, ret, how in out:
                    print(f"     {tm:%Y-%m-%d} {side_etf(d)} {why[:36]:36} entry {e:8.2f} {ret * 100:6.2f}% {how}")


if __name__ == "__main__":
    main()
