"""Network-free smoke test of deploy/executor.py: replays synthetic bars day by day against a fake broker.

    uv run python tests/make_bars.py /tmp/bars.csv && uv run python tests/executor_smoke.py /tmp/bars.csv
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Dict, List

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "deploy"))

import executor as x  # noqa: E402
import swing_frvp as m  # noqa: E402


class FakeBroker:
    """Fills market orders at once at the current price; stop orders trigger on the next bar's ETF low."""

    def __init__(self, cash: float = 10_000.0):
        self._cash = cash
        self.pos: Dict[str, float] = {}
        self.book: List[x.Order] = []
        self.now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.close_at = self.now
        self.spy = 500.0
        self.n = 0

    def px(self, sym: str) -> float:
        return {"SSO": self.spy / 10, "SDS": 10_000 / self.spy}.get(sym, self.spy)

    def clock(self):
        return self.now, True, self.close_at

    def cash(self):
        return self._cash

    def positions(self):
        return {s: q for s, q in self.pos.items() if q > 0}

    def orders(self):
        return [o for o in self.book]

    def price(self, sym):
        return self.px(sym)

    def _add(self, o: x.Order) -> x.Order:
        assert all(b.cid != o.cid for b in self.book), f"duplicate client_order_id {o.cid}"
        self.book.insert(0, o)
        return o

    def market(self, sym, qty, side, cid):
        self.n += 1
        p = self.px(sym)
        sgn = 1 if side == "buy" else -1
        held = sum(b.qty for b in self.book if b.active and b.symbol == sym and b.side == "sell")
        if side == "sell" and qty + held > self.pos.get(sym, 0) + 1e-9:
            return self._add(x.Order(str(self.n), cid, sym, side, qty, 0, None, "rejected", "market"))
        if side == "buy" and qty * p > self._cash + 1e-6:
            return self._add(x.Order(str(self.n), cid, sym, side, qty, 0, None, "rejected", "market"))
        self.pos[sym] = self.pos.get(sym, 0) + sgn * qty
        self._cash -= sgn * qty * p
        return self._add(x.Order(str(self.n), cid, sym, side, qty, qty, p, "filled", "market"))

    def stop(self, sym, qty, stop_price, cid):
        self.n += 1
        return self._add(x.Order(str(self.n), cid, sym, "sell", qty, 0, None, "new", "stop", stop_price))

    def cancel(self, oid):
        for o in self.book:
            if o.id == oid and o.active:
                o.status = "canceled"

    def wait(self, oid, timeout=0):
        return next(o for o in self.book if o.id == oid)

    def trigger_stops(self, etf_low: Dict[str, float]):
        for o in self.book:
            if o.active and o.type == "stop" and etf_low.get(o.symbol, 1e9) <= o.stop:
                p = o.stop
                self.pos[o.symbol] -= o.qty
                self._cash += o.qty * p
                o.status, o.filled_qty, o.avg = "filled", o.qty, p


def main(path: str) -> None:
    bars = m.load_csv(path)
    st = x.Settings(start=datetime(2026, 1, 1), dry_run=False, paper=True, min_t1_pct=0.0)
    br = FakeBroker()
    actions = 0
    for i in range(30, len(bars)):
        day = bars[i].time
        br.spy = bars[i].close
        br.trigger_stops({"SSO": bars[i].low / 10, "SDS": 10_000 / bars[i].high})
        for hour, pre in ((11, False), (15, True)):
            br.now = datetime(day.year, day.month, day.day, hour + 5, 45, tzinfo=timezone.utc)
            br.close_at = br.now + timedelta(minutes=15 if pre else 300)
            out = x.run(br, lambda: bars[: i + 1], st)
            actions += len(out["actions"])
            held = br.positions()
            assert len(held) <= 1, f"holding both ETFs on {day:%Y-%m-%d}: {held}"
            assert all(q > 0 for q in held.values())
            assert br.cash() >= -1e-6, "negative cash"
            for a in out["actions"]:
                print(f"{day:%Y-%m-%d} {'pre-close' if pre else 'midday   '}  {a}")
    entries = [o for o in br.book if x.Plan.parse(o.cid)]
    assert entries, "no entry order in the whole replay"
    assert all(len(o.cid) <= 128 for o in br.book)
    print(f"OK: {len(entries)} entries, {actions} actions, final cash {br.cash():.2f}, holding {br.positions()}")


if __name__ == "__main__":
    main(sys.argv[1])
