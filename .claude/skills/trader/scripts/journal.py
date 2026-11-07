"""Trade journal: record opened and closed positions in trades.csv at the repo root.

    python3 .claude/skills/trader/scripts/journal.py open SSO 78 63.85 --real true --date 2026-11-09
    python3 .claude/skills/trader/scripts/journal.py close SSO 66.30 --date 2026-11-20
    python3 .claude/skills/trader/scripts/journal.py close SSO 66.30 --amount 40     # partial: splits the row
    python3 .claude/skills/trader/scripts/journal.py close SDS 55.89 --real false --row 3
    python3 .claude/skills/trader/scripts/journal.py list                             # open and closed, with P&L

Columns: open_date,symbol,amount,buy_price,real,sell_date,close_price
amount is the number of shares. real is true for a real trade, false for a backtest/paper record.
A row is open while sell_date is empty. Dates default to today (YYYY-MM-DD).
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import date
from pathlib import Path
from typing import Dict, List

FIELDS = ["open_date", "symbol", "amount", "buy_price", "real", "sell_date", "close_price"]
DEFAULT_FILE = Path(__file__).resolve().parents[4] / "trades.csv"   # repo root


def to_bool(s: str) -> str:
    v = s.strip().lower()
    if v in ("true", "t", "yes", "y", "1", "real"):
        return "true"
    if v in ("false", "f", "no", "n", "0", "paper", "backtest"):
        return "false"
    raise argparse.ArgumentTypeError(f"real must be true or false, got {s!r}")


def num(s: str) -> str:
    """Validate a number, keep the user's text (no float rounding in the file)."""
    float(s)
    return s.strip()


def day(s: str) -> str:
    return date.fromisoformat(s.strip()).isoformat()


def load(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    return [{k: (r.get(k) or "").strip() for k in FIELDS} for r in rows]


def save(path: Path, rows: List[Dict[str, str]]) -> None:
    tmp = path.with_suffix(".csv.tmp")
    with tmp.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)


def show(i: int, r: Dict[str, str]) -> str:
    s = f"row {i}: {r['open_date']} {r['symbol']} x{r['amount']} @ {r['buy_price']} real={r['real']}"
    if r["sell_date"]:
        pnl = float(r["amount"]) * (float(r["close_price"]) - float(r["buy_price"]))
        pct = (float(r["close_price"]) / float(r["buy_price"]) - 1) * 100
        s += f" -> {r['sell_date']} @ {r['close_price']}  P&L {pnl:+.2f} ({pct:+.2f}%)"
    else:
        s += "  OPEN"
    return s


def cmd_open(a, rows, path) -> int:
    rows.append({"open_date": a.date, "symbol": a.symbol.upper(), "amount": a.amount, "buy_price": a.price,
                 "real": a.real, "sell_date": "", "close_price": ""})
    save(path, rows)
    print("opened", show(len(rows), rows[-1]))
    return 0


def cmd_close(a, rows, path) -> int:
    if a.row is not None:
        if not 1 <= a.row <= len(rows) or rows[a.row - 1]["sell_date"]:
            print(f"row {a.row} is not an open position", file=sys.stderr)
            return 1
        idx = a.row - 1
    else:
        cand = [i for i, r in enumerate(rows) if not r["sell_date"] and r["symbol"] == a.symbol.upper()
                and (a.real is None or r["real"] == a.real)]
        if not cand:
            print(f"no open {a.symbol.upper()} position" + (f" with real={a.real}" if a.real else ""), file=sys.stderr)
            return 1
        if len(cand) > 1:
            print(f"{len(cand)} open {a.symbol.upper()} positions; pass --row N (or --real):", file=sys.stderr)
            for i in cand:
                print("  " + show(i + 1, rows[i]), file=sys.stderr)
            return 2
        idx = cand[0]
    r = rows[idx]
    if a.symbol.upper() != r["symbol"]:
        print(f"row {idx + 1} is {r['symbol']}, not {a.symbol.upper()}", file=sys.stderr)
        return 1
    held = float(r["amount"])
    if a.amount is not None and float(a.amount) > held:
        print(f"can't close {a.amount}; row {idx + 1} holds {r['amount']}", file=sys.stderr)
        return 1
    if a.amount is not None and float(a.amount) < held:
        # Partial close: the closed part becomes this row, the rest stays open right after it
        rest = dict(r, amount=f"{held - float(a.amount):g}")
        r["amount"] = a.amount
        rows.insert(idx + 1, rest)
    r["sell_date"], r["close_price"] = a.date, a.price
    save(path, rows)
    print("closed", show(idx + 1, r))
    if a.amount is not None and float(a.amount) < held:
        print("still open", show(idx + 2, rows[idx + 1]))
    return 0


def cmd_list(a, rows, path) -> int:
    sel = [(i + 1, r) for i, r in enumerate(rows) if a.real is None or r["real"] == a.real]
    if not sel:
        print("no trades")
        return 0
    for i, r in sel:
        print(show(i, r))
    for flag in ("true", "false"):
        closed = [r for _, r in sel if r["real"] == flag and r["sell_date"]]
        if closed:
            pnl = [float(r["amount"]) * (float(r["close_price"]) - float(r["buy_price"])) for r in closed]
            wins = sum(p > 0 for p in pnl)
            print(f"{'real' if flag == 'true' else 'backtest'}: {len(closed)} closed, {wins} wins, total P&L {sum(pnl):+.2f}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", type=Path, default=DEFAULT_FILE, help="CSV path (default: trades.csv at the repo root)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    o = sub.add_parser("open", help="record a new position")
    o.add_argument("symbol")
    o.add_argument("amount", type=num, help="number of shares")
    o.add_argument("price", type=num, help="buy price per share")
    o.add_argument("--real", type=to_bool, required=True, help="true for a real trade, false for backtest/paper")
    o.add_argument("--date", type=day, default=date.today().isoformat())

    c = sub.add_parser("close", help="close an open position")
    c.add_argument("symbol")
    c.add_argument("price", type=num, help="close price per share")
    c.add_argument("--date", type=day, default=date.today().isoformat())
    c.add_argument("--amount", type=num, help="shares to close (default: all); fewer splits the row")
    c.add_argument("--real", type=to_bool, help="only match rows with this real value")
    c.add_argument("--row", type=int, help="1-based row number (from `list`) when several are open")

    ls = sub.add_parser("list", help="show trades and P&L")
    ls.add_argument("--real", type=to_bool)

    a = ap.parse_args()
    rows = load(a.file)
    return {"open": cmd_open, "close": cmd_close, "list": cmd_list}[a.cmd](a, rows, a.file)


if __name__ == "__main__":
    sys.exit(main())
