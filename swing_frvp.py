"""Swing Fixed Range Volume Profile: a Python port of swing_frvp.pine.

Same rules as the Pine script (profile, Value Area, HVN / LVN, profile shape, BUY / SELL
signals, targets, stops, outcome stats and the live signal), without the drawing.

    python swing_frvp.py SPY --start 2026-03-30            # needs `pip install yfinance`
    python swing_frvp.py SPY --start 2026-03-30 --csv spy.csv
"""
from __future__ import annotations

import argparse
import csv
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

STOP_VOL = "volume"   # Volume zone (behind the level's volume)
STOP_BAR = "bar"      # Signal bar (low / high)
RULE_CLOSE = "close"  # Close beyond stop, plus hard stop
RULE_TOUCH = "touch"  # Touch


# ───── Inputs (defaults match the Pine script)
@dataclass
class Config:
    start: datetime = datetime(2026, 3, 30)
    end: Optional[datetime] = None

    rows: int = 120
    va_pct: float = 70.0

    node_win: int = 5
    max_nodes: int = 3

    use_pull: bool = True
    cooldown: int = 3

    atr_len: int = 14
    min_gap_atr: float = 0.5
    tgt_buf: float = 0.1
    stop_mode: str = STOP_VOL
    low_vol_pct: float = 50.0
    stop_buf: float = 0.25
    min_stop_atr: float = 0.5
    stop_rule: str = RULE_CLOSE
    hard_mult: float = 1.5
    min_rr: float = 1.5
    max_hold: int = 5

    mintick: float = 0.01


@dataclass
class Bar:
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Signal:
    index: int          # index into the range bars
    time: datetime
    dir: int
    why: str
    level: float
    entry: float
    t1: float
    t2: float
    stop: float
    hard: float
    rr: float
    result: str         # outcome within max_hold bars
    hit1: bool = False
    hit2: bool = False
    stopped: bool = False
    status: str = ""    # outcome followed to today (latest signal only)
    ago: int = 0        # bars from the signal to the last range bar


@dataclass
class Stats:
    signals: int = 0
    t1: int = 0
    t2: int = 0
    stopped: int = 0
    expired: int = 0
    open: int = 0


@dataclass
class Profile:
    p_min: float
    p_max: float
    step: float
    up: List[float]
    dn: List[float]
    tot: List[float]
    total: float
    poc_idx: int
    va_lo: int
    va_hi: int
    poc: float
    vah: float
    val: float
    va_mean: float
    low_vol_thr: float
    hvn: List[Tuple[float, float]]   # (price, volume), every HVN in price order
    lvn: List[Tuple[float, float]]
    shape_bias: int
    shape_name: str
    shape_text: str

    def top_hvn(self, k: int) -> List[Tuple[float, float]]:
        return sorted(self.hvn, key=lambda x: -x[1])[:k]

    def top_lvn(self, k: int) -> List[Tuple[float, float]]:
        return sorted(self.lvn, key=lambda x: x[1])[:k]


@dataclass
class Result:
    profile: Optional[Profile]
    signals: List[Signal] = field(default_factory=list)   # oldest first
    stats: Stats = field(default_factory=Stats)
    live_dir: int = 0
    live_why: str = ""
    live_entry: float = math.nan
    live_t1: float = math.nan
    live_t2: float = math.nan
    live_stop: float = math.nan
    live_rr: float = math.nan
    cross_up_vah: bool = False
    cross_dn_val: bool = False
    last: Optional[Bar] = None
    bars: List[Bar] = field(default_factory=list)          # the range bars; Signal.index points here

    @property
    def latest(self) -> Optional[Signal]:
        return self.signals[-1] if self.signals else None


def atr_series(bars: List[Bar], length: int) -> List[float]:
    """ta.atr (RMA of true range, SMA seed), with nz(atr, high - low) like the script."""
    out = []
    rma = math.nan
    trs = []
    for i, b in enumerate(bars):
        tr = b.high - b.low if i == 0 else max(b.high - b.low, abs(b.high - bars[i - 1].close), abs(b.low - bars[i - 1].close))
        trs.append(tr)
        if i == length - 1:
            rma = sum(trs) / length
        elif i >= length:
            rma = (rma * (length - 1) + tr) / length
        out.append(b.high - b.low if math.isnan(rma) else rma)
    return out


class SwingFRVP:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.prof: Optional[Profile] = None
        self.lvls: List[float] = []
        self.pull_lv: List[float] = []

    # ───── Value Area rules (checked on each bar's close, against the profile levels)
    #   BUY : bounce off VAL, failed breakdown (back above VAL), retest of VAH holds
    #   SELL: rejection at VAH, failed breakout (back below VAH), retest of VAL fails
    def eval_sig(self, c, o, h, l, pc) -> Tuple[int, str, float]:
        p = self.prof
        if p is None or pc is None:
            return 0, "", math.nan
        hi, lo = p.vah, p.val
        bull, bear = c > o, c < o
        if pc < lo and c > lo and bull:
            return 1, "Failed breakdown: back above VAL", lo
        if l <= lo and c > lo and bull:
            return 1, "Bounce off VAL", lo
        if pc > hi and l <= hi and c > hi and bull:
            return 1, "Retest of VAH holds", hi
        if pc > hi and c < hi:
            return -1, "Failed breakout: back below VAH", hi
        if h >= hi and c < hi and bear:
            return -1, "Rejection at VAH", hi
        if pc < lo and h >= lo and c < lo and bear:
            return -1, "Retest of VAL fails", lo
        return 0, "", math.nan

    # Pullback rule: the first touch of the POC or an HVN in the profile's direction
    # (P-profile or thin uptrend: buy; b-profile or thin downtrend: sell). Entry at the level, no confirmation.
    def eval_pull(self, o, h, l, pc, ph, pl) -> Tuple[int, str, float, float]:
        p = self.prof
        if p is None or p.shape_bias == 0 or pc is None:
            return 0, "", math.nan, math.nan
        lvl = None
        for x in self.pull_lv:
            if p.shape_bias == 1 and pc > x and pl > x and l <= x and (lvl is None or x > lvl):
                lvl = x
            elif p.shape_bias == -1 and pc < x and ph < x and h >= x and (lvl is None or x < lvl):
                lvl = x
        if lvl is None:
            return 0, "", math.nan, math.nan
        d = p.shape_bias
        why = "Pullback to " + ("POC" if lvl == p.poc else "HVN") + " (" + p.shape_name + ")"
        return d, why, lvl, (min(o, lvl) if d == 1 else max(o, lvl))

    # All rules: the Value Area rules first, then the pullback rule. Returns direction, reason, level, entry.
    def full_sig(self, c, o, h, l, pc, ph, pl) -> Tuple[int, str, float, float]:
        d, why, lvl = self.eval_sig(c, o, h, l, pc)
        if d != 0:
            return d, why, lvl, c
        if self.cfg.use_pull:
            return self.eval_pull(o, h, l, pc, ph, pl)
        return 0, "", math.nan, math.nan

    def sig_at(self, bars: List[Bar], i: int):
        b = bars[i]
        if i < 1:
            return self.full_sig(b.close, b.open, b.high, b.low, None, None, None)
        p = bars[i - 1]
        return self.full_sig(b.close, b.open, b.high, b.low, p.close, p.high, p.low)

    # ───── Targets: the next two volume levels (POC, VAH, VAL, HVNs) in the trade's direction,
    #       placed tgt_buf ATR in front of the level since price often turns just before it.
    #       Past the last level, fall back to 2 and 4 ATR.
    def targets_for(self, px: float, d: int, atr_v: float) -> Tuple[float, float]:
        cfg = self.cfg
        t1 = t2 = None
        min_gap = cfg.min_gap_atr * atr_v
        for x in self.lvls:
            xb = x - d * cfg.tgt_buf * atr_v
            g = (xb - px) * d
            if g >= min_gap:
                if t1 is None or g < (t1 - px) * d:
                    t2 = t1
                    t1 = xb
                elif t2 is None or g < (t2 - px) * d:
                    t2 = xb
        if t1 is None:
            t1 = px + d * 2 * atr_v
        if t2 is None:
            t2 = t1 + d * 2 * atr_v
        return t1, t2

    # Stop: behind the volume zone around the signal's level, at the first low-volume row,
    # and beyond the signal bar's low/high (the swing point). At least min_stop_atr from the entry.
    def stop_for(self, ent: float, lvl: float, ext: float, d: int, atr_v: float) -> float:
        cfg = self.cfg
        if cfg.stop_mode == STOP_BAR:
            return ext - d * cfg.stop_buf * atr_v
        edge = lvl
        p = self.prof
        if p is not None and p.step > 0:
            n = len(p.tot)
            r = max(0, min(n - 1, math.floor((lvl - p.p_min) / p.step)))
            while 0 <= r < n and p.tot[r] >= p.low_vol_thr:
                r -= d
            edge = min(lvl, p.p_min + (r + 1) * p.step) if d == 1 else max(lvl, p.p_min + r * p.step)
        base = min(edge, ext) if d == 1 else max(edge, ext)
        sl = base - d * cfg.stop_buf * atr_v
        if (ent - sl) * d < cfg.min_stop_atr * atr_v:
            sl = ent - d * cfg.min_stop_atr * atr_v
        return sl

    # Close rule: out when a bar closes beyond the stop, or immediately at the hard stop
    def stop_hit(self, b: Bar, sl: float, hard: float, d: int) -> bool:
        if self.cfg.stop_rule == RULE_TOUCH:
            return b.low <= sl if d == 1 else b.high >= sl
        return (b.close < sl or b.low <= hard) if d == 1 else (b.close > sl or b.high >= hard)

    # True when the opposite setup fired within the previous `cooldown` bars (price chopping at a level)
    def choppy(self, bars: List[Bar], i: int, d: int, first: int = 1) -> bool:
        for k in range(1, self.cfg.cooldown + 1):
            if i - k >= first and self.sig_at(bars, i - k)[0] == -d:
                return True
        return False

    # ───── Profile from the range bars
    def build_profile(self, rb: List[Bar]) -> Optional[Profile]:
        cfg = self.cfg
        rows = cfg.rows
        p_min = min(b.low for b in rb)
        p_max = max(b.high for b in rb)
        step = (p_max - p_min) / rows
        if step <= 0:
            return None

        up = [0.0] * rows
        dn = [0.0] * rows
        # Spread each bar's volume across the rows its high-low range covers
        for b in rb:
            r0 = min(rows - 1, math.floor((b.low - p_min) / step))
            r1 = min(rows - 1, math.floor((b.high - p_min) / step))
            for r in range(r0, r1 + 1):
                share = (min(b.high, p_min + (r + 1) * step) - max(b.low, p_min + r * step)) / (b.high - b.low) if b.high > b.low else 1.0
                if b.close >= b.open:
                    up[r] += b.volume * share
                else:
                    dn[r] += b.volume * share

        tot = [u + v for u, v in zip(up, dn)]
        total = sum(tot)
        if total <= 0:
            return None
        max_v = max(tot)
        poc_idx = tot.index(max_v)
        mean = total / rows

        # Value Area: expand from POC toward the heavier neighbour
        hi_idx = lo_idx = poc_idx
        acc = max_v
        target = total * cfg.va_pct / 100
        while acc < target and (hi_idx < rows - 1 or lo_idx > 0):
            v_up = tot[hi_idx + 1] if hi_idx < rows - 1 else -1.0
            v_dn = tot[lo_idx - 1] if lo_idx > 0 else -1.0
            if v_up >= v_dn:
                hi_idx += 1
                acc += v_up
            else:
                lo_idx -= 1
                acc += v_dn

        poc = p_min + (poc_idx + 0.5) * step
        vah = p_min + (hi_idx + 1) * step
        val = p_min + lo_idx * step
        va_mean = acc / (hi_idx - lo_idx + 1)

        # HVN = local volume peaks, LVN = local volume gaps inside the Value Area
        # (gaps outside it are mostly empty rows left by fast moves)
        h_idx: List[int] = []
        l_idx: List[int] = []
        w = cfg.node_win
        for r in range(w, rows - w):
            rv = tot[r]
            near = [tot[k] for k in range(r - w, r + w + 1) if k != r]
            is_peak = all(ov <= rv for ov in near)
            is_trough = all(ov >= rv for ov in near)
            if is_peak and r != poc_idx and rv > mean and (not h_idx or r - h_idx[-1] > w):
                h_idx.append(r)
            if is_trough and rv > 0 and lo_idx <= r <= hi_idx and rv < va_mean * 0.75 and (not l_idx or r - l_idx[-1] > w):
                l_idx.append(r)

        # Profile shape: thin (no big volume area, a trend), P (POC high), b (POC low) or D (balance)
        poc_pos = (poc - p_min) / (p_max - p_min)
        trend_up = rb[-1].close >= rb[0].open
        if max_v < 2.0 * mean:
            bias = 1 if trend_up else -1
            name = "thin uptrend" if trend_up else "thin downtrend"
            text = "Thin, uptrend: buy pullbacks to HVN clusters" if trend_up else "Thin, downtrend: sell rallies to HVN clusters"
        elif poc_pos > 0.6:
            bias, name, text = 1, "P-profile", "P (buyers in control): POC / HVN are support on pullbacks"
        elif poc_pos < 0.4:
            bias, name, text = -1, "b-profile", "b (sellers in control): POC / HVN are resistance on rallies"
        else:
            bias, name, text = 0, "D-profile", "D (balance): buy near VAL, sell near VAH, target the POC"

        return Profile(
            p_min=p_min, p_max=p_max, step=step, up=up, dn=dn, tot=tot, total=total,
            poc_idx=poc_idx, va_lo=lo_idx, va_hi=hi_idx, poc=poc, vah=vah, val=val,
            va_mean=va_mean, low_vol_thr=va_mean * cfg.low_vol_pct / 100,
            hvn=[(p_min + (r + 0.5) * step, tot[r]) for r in h_idx],
            lvn=[(p_min + (r + 0.5) * step, tot[r]) for r in l_idx],
            shape_bias=bias, shape_name=name, shape_text=text,
        )

    def run(self, bars: List[Bar]) -> Result:
        """`bars` is the whole chart (with history before the start, for the ATR and the live signal)."""
        cfg = self.cfg
        atr = atr_series(bars, cfg.atr_len)
        in_range = [i for i, b in enumerate(bars) if b.time >= cfg.start and (cfg.end is None or b.time <= cfg.end) and b.volume is not None]
        rb = [bars[i] for i in in_range]
        ra = [atr[i] for i in in_range]
        res = Result(profile=None, last=bars[-1] if bars else None, bars=rb)
        if not rb:
            return res

        self.prof = p = self.build_profile(rb)
        res.profile = p
        if p is None:
            return res
        # Every HVN is a possible target and pullback level
        self.lvls = [p.poc, p.vah, p.val] + [x for x, _ in p.hvn]
        self.pull_lv = [p.poc] + [x for x, _ in p.hvn]

        # Signals: targets, stop and outcome of each past signal (newest first, like the script)
        n = len(rb)
        st = res.stats
        found: List[Signal] = []
        for i in range(n - 1, 0, -1):
            d, why, s_lvl, ent = self.sig_at(rb, i)
            if d == 0 or self.choppy(rb, i, d):
                continue
            av = ra[i]
            t1, t2 = self.targets_for(ent, d, av)
            sl = self.stop_for(ent, s_lvl, rb[i].low if d == 1 else rb[i].high, d, av)
            hard = ent + (sl - ent) * cfg.hard_mult
            rr = abs(t1 - ent) / max(abs(ent - sl), cfg.mintick)
            if cfg.min_rr != 0 and rr < cfg.min_rr:
                continue

            hit1 = hit2 = stopped = False
            window_done = i + cfg.max_hold <= n - 1
            for j in range(i + 1, min(n - 1, i + cfg.max_hold) + 1):
                b = rb[j]
                if self.stop_hit(b, sl, hard, d):
                    stopped = True
                    break
                if (b.high >= t1) if d == 1 else (b.low <= t1):
                    hit1 = True
                if (b.high >= t2) if d == 1 else (b.low <= t2):
                    hit2 = True
                    break
            # A trade counts once it hit something or its window ran out; otherwise it is still open
            if hit1 or stopped or window_done:
                st.signals += 1
                st.t1 += hit1
                st.t2 += hit2
                st.stopped += stopped and not hit1
                st.expired += not hit1 and not stopped
            else:
                st.open += 1
            result = ("Target 2 hit" if hit2 else
                      ("Target 1 hit, then stopped" if stopped else "Target 1 hit") if hit1 else
                      "Stopped out" if stopped else
                      f"Expired: nothing hit within {cfg.max_hold} bars" if window_done else "Open")
            found.append(Signal(i, rb[i].time, d, why, s_lvl, ent, t1, t2, sl, hard, rr, result, hit1, hit2, stopped, ago=n - 1 - i))

        if found:
            found[0].status = self.follow(rb, found[0])
        res.signals = found[::-1]
        self.live(bars, atr, res)
        return res

    # Follow the latest trade all the way to today, not just max_hold bars
    def follow(self, rb: List[Bar], s: Signal) -> str:
        b_t1 = b_t2 = b_sl = None
        d = s.dir
        for j in range(s.index + 1, len(rb)):
            b = rb[j]
            if self.stop_hit(b, s.stop, s.hard, d):
                b_sl = j - s.index
                break
            if b_t1 is None and ((b.high >= s.t1) if d == 1 else (b.low <= s.t1)):
                b_t1 = j - s.index
            if (b.high >= s.t2) if d == 1 else (b.low <= s.t2):
                b_t2 = j - s.index
                break
        if b_t2 is not None:
            return f"T2 hit after {b_t2} bars"
        if b_t1 is not None:
            return f"T1 hit after {b_t1} bars" + (f", then stopped after {b_sl}" if b_sl is not None else ", T2 still open")
        if b_sl is not None:
            return f"Stopped after {b_sl} bars"
        return "Open: no target or stop hit yet"

    # ───── Live signal on the last chart bar (drives the alerts)
    def live(self, bars: List[Bar], atr: List[float], res: Result) -> None:
        cfg = self.cfg
        p = self.prof
        i = len(bars) - 1
        b = bars[i]
        raw, why, lvl, ent = self.sig_at(bars, i)
        d = raw or 1
        e = b.close if math.isnan(ent) else ent
        t1, t2 = self.targets_for(e, d, atr[i])
        sl = self.stop_for(e, e if math.isnan(lvl) else lvl, b.low if d == 1 else b.high, d, atr[i])
        rr = abs(t1 - e) / max(abs(e - sl), cfg.mintick)
        chop = raw != 0 and self.choppy(bars, i, raw, first=0)
        res.live_dir = raw if raw != 0 and not chop and (cfg.min_rr == 0 or rr >= cfg.min_rr) else 0
        res.live_why, res.live_entry, res.live_t1, res.live_t2, res.live_stop, res.live_rr = why, e, t1, t2, sl, rr
        if i >= 1:
            pc = bars[i - 1].close
            res.cross_up_vah = b.close > p.vah and pc <= p.vah
            res.cross_dn_val = b.close < p.val and pc >= p.val


# ───── Data
def load_csv(path: str) -> List[Bar]:
    """CSV with a date/time column and open, high, low, close, volume (any case)."""
    bars = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            r = {k.strip().lower(): v for k, v in row.items()}
            ts = r.get("date") or r.get("datetime") or r.get("time") or r.get("timestamp")
            t = datetime.fromisoformat(ts.strip().replace("Z", "+00:00")).replace(tzinfo=None)
            vol = r.get("volume")
            bars.append(Bar(t, float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]), float(vol) if vol not in (None, "") else None))
    bars.sort(key=lambda b: b.time)
    return bars


def load_yf(ticker: str, start: datetime, interval: str = "1d", warmup_days: int = 60) -> List[Bar]:
    """Unadjusted bars from Yahoo (like TradingView's default), with warm-up history for the ATR."""
    import yfinance as yf

    df = yf.Ticker(ticker).history(start=start - timedelta(days=warmup_days), interval=interval, auto_adjust=False)
    bars = []
    for ts, r in df.iterrows():
        bars.append(Bar(ts.to_pydatetime().replace(tzinfo=None), float(r["Open"]), float(r["High"]), float(r["Low"]), float(r["Close"]), float(r["Volume"])))
    return bars


# ───── Report (the script's status table, plus the signal list)
def report(res: Result, cfg: Config, show_signals: int = 40) -> str:
    p = res.profile
    last = res.last
    if p is None or last is None:
        return "No volume data on this symbol (use SPY, not SPX)"
    close = last.close

    def px(v):
        return f"{v:.2f}"

    def pct_from(v, base):
        return f"{px(v)}  ({'+' if v >= base else ''}{(v / base - 1) * 100:.2f}% from entry)"

    def pct(v):
        return f"{px(v)}  ({(v / close - 1) * 100:.2f}%)"

    def vol_text(v):
        return f"vol {v:,.0f} ({v / p.total * 100:.1f}%)"

    state = ("Above value: uptrend, buy pullbacks to VAH/HVN" if close > p.vah else
             "Below value: downtrend, sell rallies to VAL/HVN" if close < p.val else
             "Inside value: range, VAL to POC to VAH")
    s = res.latest
    st = res.stats
    rows = [("State", state), ("Profile shape", p.shape_text)]
    if s is None:
        rows.append(("Latest signal", "No signal in range"))
    else:
        rows += [
            ("Latest signal", f"{'BUY' if s.dir == 1 else 'SELL'} ({s.ago} bars ago, {s.time:%Y-%m-%d}): {s.why}"),
            ("Entry", px(s.entry)),
            ("Now", pct_from(close, s.entry)),
            ("Target 1", pct_from(s.t1, s.entry)),
            ("Target 2", pct_from(s.t2, s.entry)),
            ("Stop", pct_from(s.stop, s.entry) + (f" on close | hard {px(s.hard)}" if cfg.stop_rule == RULE_CLOSE else "")),
            ("R:R to T1", f"{s.rr:.1f}"),
            ("Status (to today)", s.status),
        ]
    if st.signals == 0:
        hist = "No completed signals yet"
    else:
        nt = f"/{st.signals}"
        hist = (f"Within {cfg.max_hold} bars: T1 {st.t1}{nt} ({st.t1 * 100 / st.signals:.0f}%), "
                f"T2 {st.t2}{nt} ({st.t2 * 100 / st.signals:.0f}%), stopped {st.stopped}{nt}, expired {st.expired}{nt}"
                + (f", still open {st.open}" if st.open else ""))
    rows += [("History", hist), ("VAH", pct(p.vah)), ("POC", pct(p.poc) + "  " + vol_text(p.tot[p.poc_idx])), ("VAL", pct(p.val))]
    for x, v in sorted(p.top_hvn(cfg.max_nodes), reverse=True):
        rows.append(("HVN", pct(x) + "  " + vol_text(v)))
    for x, v in sorted(p.top_lvn(cfg.max_nodes), reverse=True):
        rows.append(("LVN", pct(x) + "  " + vol_text(v)))
    live = {1: "BUY", -1: "SELL"}.get(res.live_dir, "none")
    rows.append(("Live (last bar)", live + (f": {res.live_why}" if res.live_dir else "")))
    alerts = [a for a, on in (("Breakout above VAH", res.cross_up_vah), ("Breakdown below VAL", res.cross_dn_val)) if on]
    if alerts:
        rows.append(("Alerts", ", ".join(alerts)))

    w = max(len(k) for k, _ in rows)
    out = [f"{last.time:%Y-%m-%d %H:%M}  close {px(close)}", ""]
    out += [f"{k.ljust(w)}  {v}" for k, v in rows]
    if show_signals and res.signals:
        out += ["", f"Signals (last {min(show_signals, len(res.signals))} of {len(res.signals)}):"]
        for sg in res.signals[-show_signals:]:
            out.append(f"  {sg.time:%Y-%m-%d %H:%M}  {'BUY ' if sg.dir == 1 else 'SELL'}  entry {px(sg.entry)}  T1 {px(sg.t1)}  T2 {px(sg.t2)}  "
                       f"stop {px(sg.stop)}  R:R {sg.rr:.1f}  {sg.why}  ->  {sg.result}")
    return "\n".join(out)


# ───── Chart: candles, profile histogram on the right, levels, signals and the latest trade plan
UP_COL, DN_COL = "#26a69a", "#ef5350"
POC_COL, VA_COL, HVN_COL, LVN_COL = "#d4a017", "#2962ff", "#ff9800", "#9e9e9e"
BUY_COL, SELL_COL = "#2e7d32", "#d32f2f"


def _short_vol(v: float) -> str:
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if v >= div:
            return f"{v / div:.1f}{suf}"
    return f"{v:.0f}"


def plot(res: Result, cfg: Config, title: str = "", path: Optional[str] = None, show: bool = True,
         width_bars: int = 40, right_gap: int = 8, max_signals: int = 40, show_table: bool = True):
    import matplotlib
    if not show:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter, MaxNLocator

    p = res.profile
    rb = res.bars
    if p is None or not rb:
        raise ValueError("No profile to plot (no volume data in the range)")
    n = len(rb)
    fig, ax = plt.subplots(figsize=(15, 9))

    # Candles, on bar positions so weekends and nights leave no gaps
    for i, b in enumerate(rb):
        col = UP_COL if b.close >= b.open else DN_COL
        ax.vlines(i, b.low, b.high, color=col, linewidth=0.8, zorder=3)
        lo, hi = sorted((b.open, b.close))
        ax.bar(i, max(hi - lo, cfg.mintick), bottom=lo, width=0.6, color=col, zorder=3)

    # Value Area shading
    ax.axhspan(p.val, p.vah, xmin=0, xmax=1, color=VA_COL, alpha=0.06, zorder=0)

    # Histogram: price on the y axis, volume as bar length, right of the last bar
    x_anchor = n - 1 + right_gap + width_bars
    max_v = p.tot[p.poc_idx]
    ys = [p.p_min + (r + 0.5) * p.step for r in range(len(p.tot))]
    w_up = [u / max_v * width_bars for u in p.up]
    w_all = [t / max_v * width_bars for t in p.tot]
    alpha = [0.65 if p.va_lo <= r <= p.va_hi else 0.3 for r in range(len(p.tot))]
    for r, y in enumerate(ys):
        if p.tot[r] <= 0:
            continue
        x0 = x_anchor - w_all[r]
        ax.barh(y, w_up[r], left=x0, height=p.step, color=UP_COL, alpha=alpha[r], linewidth=0, zorder=2)
        ax.barh(y, w_all[r] - w_up[r], left=x0 + w_up[r], height=p.step, color=DN_COL, alpha=alpha[r], linewidth=0, zorder=2)

    # Levels, labelled right of the histogram
    lab_x = x_anchor + 1

    def level(price, col, name, lw, ls, vol=None):
        ax.hlines(price, 0, x_anchor, color=col, linewidth=lw, linestyle=ls, zorder=4)
        txt = f"{name} {price:.2f}" + (f" | {_short_vol(vol)} ({vol / p.total * 100:.1f}%)" if vol is not None else "")
        ax.text(lab_x, price, txt, color=col, fontsize=8, va="center", ha="left", zorder=6,
                bbox=dict(facecolor="white", edgecolor="none", pad=1, alpha=0.85))

    level(p.poc, POC_COL, "POC", 2, "-", max_v)
    level(p.vah, VA_COL, "VAH", 1, "-")
    level(p.val, VA_COL, "VAL", 1, "-")
    for x, v in p.top_hvn(cfg.max_nodes):
        level(x, HVN_COL, "HVN", 1, "--", v)
    for x, v in p.top_lvn(cfg.max_nodes):
        level(x, LVN_COL, "LVN", 1, ":", v)

    # Signals
    pad = (p.p_max - p.p_min) * 0.012
    for s in res.signals[-max_signals:]:
        b = rb[s.index]
        if s.dir == 1:
            ax.annotate("BUY", (s.index, b.low - pad), ha="center", va="top", fontsize=7, color="white", fontweight="bold", zorder=7,
                        bbox=dict(boxstyle="round,pad=0.25", facecolor=BUY_COL, edgecolor="none"))
        else:
            ax.annotate("SELL", (s.index, b.high + pad), ha="center", va="bottom", fontsize=7, color="white", fontweight="bold", zorder=7,
                        bbox=dict(boxstyle="round,pad=0.25", facecolor=SELL_COL, edgecolor="none"))

    # Trade plan for the latest signal: reward and risk zones plus target/stop lines
    s = res.latest
    if s is not None:
        x1, x2 = s.index, n
        ax.fill_between([x1, x2], min(s.entry, s.t1), max(s.entry, s.t1), color=BUY_COL, alpha=0.12, linewidth=0, zorder=1)
        ax.fill_between([x1, x2], min(s.entry, s.stop), max(s.entry, s.stop), color=SELL_COL, alpha=0.12, linewidth=0, zorder=1)
        ax.hlines(s.entry, x1, x2, color="gray", linewidth=1, zorder=5)
        ax.hlines(s.t1, x1, x2, color=BUY_COL, linewidth=2, zorder=5)
        ax.hlines(s.t2, x1, x2, color=BUY_COL, linewidth=1, linestyle="--", zorder=5)
        ax.hlines(s.stop, x1, x2, color=SELL_COL, linewidth=2, zorder=5)
        # Labels at the right end of the plan, clear of the signal tag; the stop label sits on the entry side
        # of its line and the hard stop label on the far side, so the two don't collide
        near, far = ("bottom", "top") if s.dir == 1 else ("top", "bottom")
        lab = dict(fontsize=8, ha="right", zorder=6, bbox=dict(facecolor="white", edgecolor="none", pad=0.5, alpha=0.7))
        ax.text(x2, s.t1, f"T1 {s.t1:.2f}", color=BUY_COL, va="bottom", **lab)
        ax.text(x2, s.t2, f"T2 {s.t2:.2f}", color=BUY_COL, va="bottom", **lab)
        ax.text(x2, s.stop, f"Stop {s.stop:.2f}" + (" (on close)" if cfg.stop_rule == RULE_CLOSE else ""), color=SELL_COL, va=near, **lab)
        if cfg.stop_rule == RULE_CLOSE:
            ax.hlines(s.hard, x1, x2, color=SELL_COL, linewidth=1, linestyle=":", zorder=5)
            ax.text(x2, s.hard, f"Hard stop {s.hard:.2f}", color=SELL_COL, va=far, **lab)

    # Status box (the script's table, short form), under the chart
    if show_table:
        close = res.last.close
        state = "Above value" if close > p.vah else "Below value" if close < p.val else "Inside value"
        st = res.stats
        lines = [f"State: {state}", f"Shape: {p.shape_name}"]
        if s is not None:
            lines += [f"Latest: {'BUY' if s.dir == 1 else 'SELL'} {s.ago} bars ago, {s.why}",
                      f"Entry {s.entry:.2f}  T1 {s.t1:.2f}  T2 {s.t2:.2f}  Stop {s.stop:.2f}  R:R {s.rr:.1f}",
                      f"Status: {s.status}"]
        if st.signals:
            lines.append(f"Within {cfg.max_hold} bars: T1 {st.t1}/{st.signals}, T2 {st.t2}/{st.signals}, "
                         f"stopped {st.stopped}/{st.signals}, expired {st.expired}/{st.signals}")
        # Below the chart, so it never covers price
        fig.text(0.01, 0.01, "\n".join(lines), fontsize=8, family="monospace", va="bottom", ha="left",
                bbox=dict(facecolor="white", edgecolor="#cccccc", alpha=0.9, pad=5))

    # Axes: dates under the bars, room on the right for the histogram and labels
    fmt = "%Y-%m-%d" if all(b.time.hour == 0 and b.time.minute == 0 for b in rb) else "%m-%d %H:%M"
    ax.xaxis.set_major_locator(MaxNLocator(10, integer=True))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda x, _: rb[int(x)].time.strftime(fmt) if 0 <= int(x) < n else ""))
    ax.set_xlim(-1, x_anchor + 22)
    plan = [s.t1, s.t2, s.stop, s.hard] if s is not None else []
    ax.set_ylim(min([p.p_min] + plan) - 4 * pad, max([p.p_max] + plan) + 4 * pad)
    ax.yaxis.tick_right()
    ax.grid(alpha=0.15)
    ax.set_title(title, loc="left", fontsize=11)
    fig.tight_layout(rect=(0, 0.13 if show_table else 0, 1, 1))
    if path:
        fig.savefig(path, dpi=130)
    if show:
        plt.show()
    return fig


def main() -> None:
    ap = argparse.ArgumentParser(description="Swing Fixed Range Volume Profile signals")
    ap.add_argument("ticker")
    ap.add_argument("--start", default="2026-03-30", help="profile start date (YYYY-MM-DD)")
    ap.add_argument("--end", help="optional profile end date")
    ap.add_argument("--csv", help="read bars from a CSV instead of Yahoo")
    ap.add_argument("--interval", default="1d", help="Yahoo bar interval, e.g. 1d, 1h")
    ap.add_argument("--rows", type=int, default=120)
    ap.add_argument("--max-hold", type=int, default=5)
    ap.add_argument("--min-rr", type=float, default=1.5)
    ap.add_argument("--cooldown", type=int, default=3)
    ap.add_argument("--stop-mode", choices=[STOP_VOL, STOP_BAR], default=STOP_VOL)
    ap.add_argument("--stop-rule", choices=[RULE_CLOSE, RULE_TOUCH], default=RULE_CLOSE)
    ap.add_argument("--no-pullback", action="store_true")
    ap.add_argument("--signals", type=int, default=40, help="how many recent signals to list")
    ap.add_argument("--plot", action="store_true", help="show the chart in a window")
    ap.add_argument("--save", help="save the chart to this image file (e.g. spy.png)")
    a = ap.parse_args()

    cfg = Config(
        start=datetime.fromisoformat(a.start), end=datetime.fromisoformat(a.end) if a.end else None,
        rows=a.rows, max_hold=a.max_hold, min_rr=a.min_rr, cooldown=a.cooldown,
        stop_mode=a.stop_mode, stop_rule=a.stop_rule, use_pull=not a.no_pullback,
    )
    bars = load_csv(a.csv) if a.csv else load_yf(a.ticker, cfg.start, a.interval)
    res = SwingFRVP(cfg).run(bars)
    print(report(res, cfg, a.signals))
    if a.plot or a.save:
        plot(res, cfg, title=f"{a.ticker} {a.interval}  Swing FRVP from {cfg.start:%Y-%m-%d}", path=a.save, show=a.plot, max_signals=a.signals)


if __name__ == "__main__":
    main()
