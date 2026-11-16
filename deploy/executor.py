"""Alpaca executor for the swing_frvp model: trades HOWTO.md's SSO/SDS rules automatically.

Runs as an AWS Lambda function on a schedule (hourly in market hours, see deploy/terraform) or locally:

    ALPACA_API_KEY_ID=... ALPACA_SECRET_KEY=... uv run --with alpaca-py python deploy/executor.py

Each run reconciles the model with the Alpaca account and is safe to repeat:

- Exits of the open position, decided on SPY (HOWTO "How to adapt the trade"): hard stop touched,
  a daily close beyond the stop (on the run within PRE_CLOSE_MINUTES of the close, or the first run
  after a closed bar), T1 (sells T1_FRACTION), T2, and the time stop. A GTC stop order on the ETF,
  translated from the SPY hard stop, protects the position between runs.
- Entries, all in: a pullback signal (rules 7-8) on the run after its touch, a Value Area signal
  (rules 1-6) on the run just before the close, or the latest closed-bar signal that wasn't entered
  (HOWTO "If you missed the close": SPY between the stop and T1). Same-direction signals are ignored
  while in a trade; an opposite one closes the position and flips.

Alpaca is the only state. Every order this executor sends has a client_order_id starting with
"frvp-<signal date>-<B|S>"; the entry order's id also stores the signal's SPY exits, and Alpaca
rejects a reused id, so a signal can't be entered twice. Positions in SSO/SDS without such an entry
order are left alone, and no new trade is opened while one is held.

DRY_RUN=true (the default) logs the orders instead of sending them.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Tuple

from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import swing_frvp as m  # noqa: E402

log = logging.getLogger("frvp")
ET = ZoneInfo("America/New_York")
PREFIX = "frvp"
LEVERAGE = 2.0                          # SSO / SDS move about 2x SPY's daily % move
ETF = {1: m.ETF_BUY, -1: m.ETF_SELL}    # BUY -> SSO, SELL -> SDS
SIDE = {1: "B", -1: "S"}
FINAL = {"filled", "canceled", "expired", "rejected", "replaced"}


def env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    return default if v in (None, "") else v.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    ticker: str = "SPY"
    start: datetime = datetime(2026, 3, 30)  # profile anchor, the model's default
    dry_run: bool = True
    paper: bool = True
    allocation: float = 0.98                 # share of cash put in the ETF ("all in", minus room for slippage)
    t1_fraction: float = 0.5                 # share of the position sold at T1; the rest is held for T2
    min_t1_pct: float = 1.0                  # HOWTO "When to skip a trade": T1 less than ~1% from entry
    pre_close_minutes: int = 20              # a run this close to the close acts on the day's close

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.environ.get
        return cls(
            ticker=e("TICKER") or "SPY",
            start=datetime.fromisoformat(e("PROFILE_START") or "2026-03-30"),
            dry_run=env_bool("DRY_RUN", True),
            paper=env_bool("ALPACA_PAPER", True),
            allocation=float(e("ALLOCATION") or 0.98),
            t1_fraction=float(e("T1_FRACTION") or 0.5),
            min_t1_pct=float(e("MIN_T1_PCT") or 1.0),
            pre_close_minutes=int(e("PRE_CLOSE_MINUTES") or 20),
        )


@dataclass
class Plan:
    """One trade's SPY exits, kept in its entry order's client_order_id."""
    day: date
    dir: int
    entry: float
    t1: float
    t2: float
    stop: float
    hard: float
    k: int          # time stop: bars after the signal bar (0 = none)
    spy: float      # SPY price when the entry order was sent (for the ETF stop)
    why: str = ""

    @property
    def key(self) -> str:
        return f"{PREFIX}-{self.day:%Y%m%d}-{SIDE[self.dir]}"

    @property
    def etf(self) -> str:
        return ETF[self.dir]

    def cid(self) -> str:
        return (f"{self.key}-e{self.entry:.2f}-a{self.t1:.2f}-b{self.t2:.2f}-s{self.stop:.2f}"
                f"-h{self.hard:.2f}-k{self.k}-x{self.spy:.2f}")

    _RE = re.compile(r"^frvp-(\d{8})-([BS])-e([\d.]+)-a([\d.]+)-b([\d.]+)-s([\d.]+)-h([\d.]+)-k(\d+)-x([\d.]+)$")

    @classmethod
    def parse(cls, cid: str) -> Optional["Plan"]:
        g = cls._RE.match(cid or "")
        if not g:
            return None
        f = [float(x) for x in g.groups()[2:7]]
        return cls(datetime.strptime(g.group(1), "%Y%m%d").date(), 1 if g.group(2) == "B" else -1,
                   *f, k=int(g.group(8)), spy=float(g.group(9)))

    def text(self) -> str:
        return (f"{m.side_etf(self.dir)} signal {self.day}{(': ' + self.why) if self.why else ''} | SPY entry {self.entry:.2f}, "
                f"T1 {self.t1:.2f}, T2 {self.t2:.2f}, stop {self.stop:.2f} on close, hard {self.hard:.2f}, "
                f"time stop {self.k or '-'} bars")


@dataclass
class Order:
    id: str
    cid: str
    symbol: str
    side: str
    qty: float
    filled_qty: float
    avg: Optional[float]
    status: str
    type: str
    stop: Optional[float] = None

    @property
    def active(self) -> bool:
        return self.status not in FINAL


# ───── Broker: Alpaca, plus a dry-run wrapper that only logs the orders
class AlpacaBroker:
    def __init__(self, key: str, secret: str, paper: bool):
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.trading.client import TradingClient
        self.tc = TradingClient(key, secret, paper=paper)
        self.dc = StockHistoricalDataClient(key, secret)

    @staticmethod
    def _o(o) -> Order:
        def val(x):
            return str(getattr(x, "value", x) or "")
        return Order(id=str(o.id), cid=o.client_order_id or "", symbol=o.symbol, side=val(o.side),
                     qty=float(o.qty or 0), filled_qty=float(o.filled_qty or 0),
                     avg=float(o.filled_avg_price) if o.filled_avg_price else None, status=val(o.status),
                     type=val(getattr(o, "order_type", None) or o.type),
                     stop=float(o.stop_price) if o.stop_price else None)

    def clock(self) -> Tuple[datetime, bool, datetime]:
        c = self.tc.get_clock()
        return c.timestamp, c.is_open, c.next_close

    def cash(self) -> float:
        return float(self.tc.get_account().cash)

    def positions(self) -> Dict[str, float]:
        return {p.symbol: float(p.qty) for p in self.tc.get_all_positions()}

    def orders(self) -> List[Order]:
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest
        req = GetOrdersRequest(status=QueryOrderStatus.ALL, limit=500, symbols=list(ETF.values()),
                               after=datetime.now(timezone.utc) - timedelta(days=400))
        return [self._o(o) for o in self.tc.get_orders(filter=req) if (o.client_order_id or "").startswith(PREFIX + "-")]

    def price(self, symbol: str) -> float:
        from alpaca.data.requests import StockLatestTradeRequest
        return float(self.dc.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=symbol))[symbol].price)

    def market(self, symbol: str, qty: int, side: str, cid: str) -> Order:
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest
        return self._o(self.tc.submit_order(MarketOrderRequest(
            symbol=symbol, qty=qty, side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY, client_order_id=cid)))

    def stop(self, symbol: str, qty: int, stop_price: float, cid: str) -> Order:
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import StopOrderRequest
        return self._o(self.tc.submit_order(StopOrderRequest(
            symbol=symbol, qty=qty, side=OrderSide.SELL, time_in_force=TimeInForce.GTC,
            stop_price=round(stop_price, 2), client_order_id=cid)))

    def cancel(self, order_id: str) -> None:
        self.tc.cancel_order_by_id(order_id)

    def get(self, order_id: str) -> Order:
        return self._o(self.tc.get_order_by_id(order_id))

    def wait(self, order_id: str, timeout: float = 20.0) -> Order:
        """Poll until the order is filled, canceled or otherwise final."""
        end = time.time() + timeout
        o = self.get(order_id)
        while o.active and time.time() < end:
            time.sleep(1)
            o = self.get(order_id)
        return o


class DryRun:
    """Reads from the real account, logs the orders instead of sending them."""

    def __init__(self, inner):
        self.inner = inner
        self.n = 0

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def _fake(self, symbol, qty, side, cid, typ, stop=None, avg=None) -> Order:
        self.n += 1
        return Order(f"dry-{self.n}", cid, symbol, side, qty, qty if avg else 0, avg, "filled" if avg else "new", typ, stop)

    def market(self, symbol, qty, side, cid):
        return self._fake(symbol, qty, side, cid, "market", avg=self.inner.price(symbol))

    def stop(self, symbol, qty, stop_price, cid):
        return self._fake(symbol, qty, "sell", cid, "stop", stop=round(stop_price, 2))

    def cancel(self, order_id):
        pass

    def wait(self, order_id, timeout=20.0):
        return Order(order_id, "", "", "", 0, 0, None, "canceled", "")


# ───── Decisions
def hard_touched(b: m.Bar, p: Plan) -> bool:
    return b.low <= p.hard if p.dir == 1 else b.high >= p.hard


def touched(b: m.Bar, level: float, d: int) -> bool:
    return b.high >= level if d == 1 else b.low <= level


def exit_check(eng: m.SwingFRVP, p: Plan, after: List[m.Bar], today: date, pre_close: bool) -> Tuple[Optional[str], bool]:
    """Walk the bars after the signal bar like SwingFRVP.follow(). Returns (exit reason, T1 hit).
    A bar's close counts once the bar is closed, or on today's bar when the run is just before the close."""
    t1_hit = False
    for b in after:
        final = b.time.date() < today or pre_close
        if final and eng.stop_hit(b, p.stop, p.hard, p.dir):
            return (f"hard stop {p.hard:.2f} touched" if hard_touched(b, p) else f"close {b.close:.2f} beyond stop {p.stop:.2f}"), t1_hit
        if not final and hard_touched(b, p):
            return f"hard stop {p.hard:.2f} touched", t1_hit
        if touched(b, p.t1, p.dir):
            t1_hit = True
        if touched(b, p.t2, p.dir):
            return f"T2 {p.t2:.2f} hit", True
    n = len(after)  # bars since the signal bar, today's included
    if not t1_hit and p.k and (n > p.k or (n == p.k and pre_close)):
        return f"time stop: T1 not reached {p.k} bars after the signal", False
    return None, t1_hit


def etf_stop_price(p: Plan, etf_fill: float) -> float:
    """The SPY hard stop as an ETF price, from the ETF fill and SPY's price at entry (approximate: daily reset)."""
    return round(etf_fill * (1 + LEVERAGE * p.dir * (p.hard - p.spy) / p.spy), 2)


class Run:
    def __init__(self, broker, st: Settings):
        self.b = broker
        self.st = st
        self.orders: List[Order] = []
        self.used: set = set()
        self.actions: List[str] = []    # orders sent or canceled
        self.notes: List[str] = []

    def act(self, msg: str) -> None:
        log.info("ACTION %s", msg)
        self.actions.append(msg)

    def note(self, msg: str) -> None:
        log.info("%s", msg)
        self.notes.append(msg)

    def next_id(self, base: str) -> str:
        n = sum(1 for o in self.orders if o.cid.startswith(base)) + sum(1 for c in self.used if c.startswith(base)) + 1
        cid = f"{base}{n}"
        self.used.add(cid)
        return cid

    def sym_orders(self, sym: str, active: bool = True) -> List[Order]:
        return [o for o in self.b.orders() if o.symbol == sym and (o.active or not active)]

    def cancel_all(self, sym: str, only_stops: bool = False) -> None:
        for o in self.sym_orders(sym):
            if only_stops and o.type != "stop":
                continue
            self.b.cancel(o.id)
            self.b.wait(o.id)
            self.act(f"canceled {o.type} {o.side} {o.qty:g} {sym} ({o.cid})")

    def sell(self, sym: str, qty: int, cid: str, why: str) -> Optional[Order]:
        if qty < 1:
            return None
        o = self.b.wait(self.b.market(sym, qty, "sell", cid).id) if not self.st.dry_run else self.b.market(sym, qty, "sell", cid)
        self.act(f"sell {qty} {sym} at market: {why} ({cid}, {o.status})")
        return o

    def close_position(self, sym: str, key: str, why: str) -> None:
        self.cancel_all(sym)
        qty = int(self.b.positions().get(sym, 0))
        self.sell(sym, qty, self.next_id(key + "-x"), why)

    def ensure_stop(self, sym: str, p: Plan, etf_fill: Optional[float], qty: Optional[int] = None) -> None:
        if not etf_fill:
            return
        q = int(self.b.positions().get(sym, 0)) if qty is None else qty
        if q < 1:
            return
        px = etf_stop_price(p, etf_fill)
        stops = [o for o in self.sym_orders(sym) if o.type == "stop"]
        if len(stops) == 1 and int(stops[0].qty) == q and stops[0].stop is not None and abs(stops[0].stop - px) < 0.005:
            return
        if not self.st.dry_run:
            self.cancel_all(sym, only_stops=True)
        o = self.b.stop(sym, q, px, self.next_id(p.key + "-h"))
        self.act(f"GTC stop sell {q} {sym} at {px:.2f} (SPY hard stop {p.hard:.2f}) ({o.cid}, {o.status})")


def bars_after(bars: List[m.Bar], day: date) -> List[m.Bar]:
    return [b for b in bars if b.time.date() > day]


def run(broker, load_bars: Callable[[], List[m.Bar]], st: Settings, assume_open: bool = False) -> Dict:
    r = Run(broker, st)
    now, is_open, next_close = broker.clock()
    if not is_open and not assume_open:
        r.note("Market closed: nothing to do")
        return {"actions": r.actions, "notes": r.notes}
    today = now.astimezone(ET).date()
    pre_close = assume_open or (next_close - now) <= timedelta(minutes=st.pre_close_minutes)
    cfg = m.Config(start=st.start)
    eng = m.SwingFRVP(cfg)

    bars = load_bars()
    closed = [b for b in bars if b.time.date() < today]
    if bars and bars[-1].time.date() == today:
        today_bar = bars[-1]
        spy = today_bar.close
    else:
        spy = broker.price(st.ticker)
        today_bar = m.Bar(datetime(today.year, today.month, today.day), spy, spy, spy, spy, 0.0)
        r.note(f"No {today} bar from the data source yet; using the last trade {spy:.2f} for today")
    seen = closed + [today_bar]
    res_c = m.SwingFRVP(cfg).run(closed)
    res_l = m.SwingFRVP(cfg).run(closed + [today_bar]) if today_bar.volume else None
    r.note(f"{now.astimezone(ET):%Y-%m-%d %H:%M} ET, {st.ticker} {spy:.2f}{', pre-close run' if pre_close else ''}"
           f"{', DRY RUN' if st.dry_run else ''}{', paper' if st.paper else ', LIVE'}")

    r.orders = broker.orders()
    held = {s: q for s, q in broker.positions().items() if s in ETF.values() and q > 0}
    entries = sorted([(Plan.parse(o.cid), o) for o in r.orders if Plan.parse(o.cid)], key=lambda x: x[0].day, reverse=True)

    # ── Exits of what is held
    managed: Dict[str, Tuple[Plan, Order]] = {}
    unmanaged = False
    for sym, q in held.items():
        ent = next(((p, o) for p, o in entries if o.symbol == sym and o.filled_qty > 0), None)
        if ent is None:
            r.note(f"{q:g} {sym} not opened by this executor: left alone, and no new trades while it's held")
            unmanaged = True
            continue
        p, o = ent
        r.note(f"Holding {q:g} {sym} from the {p.text()}")
        reason, t1_hit = exit_check(eng, p, bars_after(seen, p.day), today, pre_close)
        if reason:
            r.close_position(sym, p.key, reason)
            continue
        if t1_hit and not any(x.cid.startswith(p.key + "-t") and x.status not in ("canceled", "expired", "rejected") for x in r.orders):
            n = math.floor(q * st.t1_fraction)
            if n >= 1:
                if not st.dry_run:
                    r.cancel_all(sym, only_stops=True)
                r.sell(sym, n, r.next_id(p.key + "-t"), f"T1 {p.t1:.2f} hit, selling {st.t1_fraction:.0%}")
                r.ensure_stop(sym, p, o.avg, int(q) - n)
                managed[sym] = (p, o)
                continue
        r.ensure_stop(sym, p, o.avg)
        managed[sym] = (p, o)

    # ── Entry: today's live signal (pullbacks on any run, Value Area rules just before the close),
    #    else the latest closed-bar signal that is still open (late entry)
    cand: Optional[Plan] = None
    if res_l is not None and res_l.live_dir and (pre_close or res_l.live_why.startswith("Pullback")):
        e = res_l.live_entry
        cand = Plan(today, res_l.live_dir, e, res_l.live_t1, res_l.live_t2, res_l.live_stop,
                    e + (res_l.live_stop - e) * cfg.hard_mult, m.t1_within(res_l.t1_bars, 0.8) or 0, spy, res_l.live_why)
    elif res_c.latest is not None and res_c.latest.status.startswith("Open"):
        s = res_c.latest
        cand = Plan(s.time.date(), s.dir, s.entry, s.t1, s.t2, s.stop, s.hard, m.t1_within(res_c.t1_bars, 0.8) or 0, spy, s.why)
        reason, t1_hit = exit_check(eng, cand, bars_after(seen, cand.day), today, pre_close)
        if reason or t1_hit:
            r.note(f"Latest signal not entered: {reason or 'T1 already reached'} ({cand.text()})")
            cand = None
        elif cand.k and len(bars_after(seen, cand.day)) >= cand.k:
            r.note(f"Latest signal not entered: its time stop is today ({cand.text()})")
            cand = None
    if cand is None:
        r.note("No signal to enter")
        return {"actions": r.actions, "notes": r.notes}

    d = cand.dir
    if unmanaged:
        r.note(f"Not entering while an outside position is held ({cand.text()})")
        return {"actions": r.actions, "notes": r.notes}
    if any(o.cid.startswith(cand.key + "-e") for o in r.orders):
        r.note(f"Already entered (or tried) {cand.key}")
        return {"actions": r.actions, "notes": r.notes}
    if not (d * (spy - cand.stop) > 0 and d * (cand.t1 - spy) > 0):
        r.note(f"Not entering: SPY {spy:.2f} is not between the stop and T1 ({cand.text()})")
        return {"actions": r.actions, "notes": r.notes}
    if abs(cand.t1 - cand.entry) / cand.entry * 100 < st.min_t1_pct:
        r.note(f"Skipped: T1 is less than {st.min_t1_pct:g}% from entry ({cand.text()})")
        return {"actions": r.actions, "notes": r.notes}
    if cand.etf in managed:
        r.note(f"Same-direction signal while holding {cand.etf}: ignored, original exits kept ({cand.text()})")
        return {"actions": r.actions, "notes": r.notes}
    for sym, (p, _) in managed.items():
        r.close_position(sym, p.key, f"opposite signal: flipping to {cand.etf}")

    px = broker.price(cand.etf)
    cash = broker.cash()
    if st.dry_run:
        cash += sum(q * broker.price(s) for s, q in held.items() if s in managed)
    qty = math.floor(cash * st.allocation / px)
    if qty < 1:
        r.note(f"Not enough cash ({cash:.2f}) for one {cand.etf} at {px:.2f}")
        return {"actions": r.actions, "notes": r.notes}
    o = broker.market(cand.etf, qty, "buy", cand.cid())
    if not st.dry_run:
        o = broker.wait(o.id)
    r.act(f"buy {qty} {cand.etf} at market (~{px:.2f}): {cand.text()} ({o.cid}, {o.status})")
    if o.status == "filled":
        r.ensure_stop(cand.etf, cand, o.avg, qty if st.dry_run else None)
    return {"actions": r.actions, "notes": r.notes}


# ───── Lambda entry point
def _notify(subject: str, body: str) -> None:
    arn = os.environ.get("NOTIFY_TOPIC_ARN")
    if not arn:
        return
    import boto3
    boto3.client("sns").publish(TopicArn=arn, Subject=subject[:100], Message=body)


def handler(event=None, context=None) -> Dict:
    logging.getLogger().setLevel(logging.INFO)
    os.environ.setdefault("XDG_CACHE_HOME", "/tmp")   # yfinance's cache; Lambda can only write to /tmp
    st = Settings.from_env()
    assume_open = bool((event or {}).get("assume_open"))
    if assume_open:
        st.dry_run = True   # a test run outside market hours never sends orders
    broker = AlpacaBroker(os.environ["ALPACA_API_KEY_ID"], os.environ["ALPACA_SECRET_KEY"], st.paper)
    if st.dry_run:
        broker = DryRun(broker)

    def load():
        try:
            import yfinance as yf
            yf.set_tz_cache_location("/tmp/yf-cache")
        except Exception:  # noqa: BLE001
            pass
        return m.load_yf(st.ticker, st.start)

    try:
        out = run(broker, load, st, assume_open=assume_open)
    except Exception as e:
        log.exception("executor failed")
        _notify("frvp executor FAILED", f"{type(e).__name__}: {e}")
        raise
    log.info(json.dumps(out))
    if out["actions"]:
        _notify(("[DRY RUN] " if st.dry_run else "") + "frvp: " + out["actions"][-1], "\n".join(out["actions"] + [""] + out["notes"]))
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print(json.dumps(handler({"assume_open": "--assume-open" in sys.argv}), indent=2))
