"""NYSE trading-day calendar: count bars, find the date N bars ahead, list option expiries.

    python trading_days.py add 15                       # date of bar 15 after today's close
    python trading_days.py add 15 --from 2026-11-02
    python trading_days.py count 2026-11-02 2026-12-18  # trading days after FROM, up to and including TO
    python trading_days.py expiries --from 2026-11-02 --weeks 10

Holidays follow NYSE rules (New Year, MLK, Presidents, Good Friday, Memorial, Juneteenth,
Independence, Labor, Thanksgiving, Christmas, with weekend observance). One-off closures
(e.g. national days of mourning) are not known.
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
from typing import List, Set


def _easter(y: int) -> date:
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(y, month, day)


def _nth_weekday(y: int, month: int, weekday: int, n: int) -> date:
    d = date(y, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last_weekday(y: int, month: int, weekday: int) -> date:
    d = date(y + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: date) -> date:
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def holidays(y: int) -> Set[date]:
    h = {
        _nth_weekday(y, 1, 0, 3),            # MLK
        _nth_weekday(y, 2, 0, 3),            # Presidents
        _easter(y) - timedelta(days=2),      # Good Friday
        _last_weekday(y, 5, 0),              # Memorial
        _observed(date(y, 7, 4)),
        _nth_weekday(y, 9, 0, 1),            # Labor
        _nth_weekday(y, 11, 3, 4),           # Thanksgiving
        _observed(date(y, 12, 25)),
    }
    ny = date(y, 1, 1)
    if ny.weekday() != 5:                    # NYSE does not close the Friday before a Saturday New Year
        h.add(_observed(ny))
    if y >= 2022:
        h.add(_observed(date(y, 6, 19)))     # Juneteenth
    return h


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in holidays(d.year)


def add_trading_days(start: date, n: int) -> date:
    d = start
    while n > 0:
        d += timedelta(days=1)
        n -= is_trading_day(d)
    return d


def count_trading_days(start: date, end: date) -> int:
    """Trading days after `start`, up to and including `end`."""
    n, d = 0, start
    while d < end:
        d += timedelta(days=1)
        n += is_trading_day(d)
    return n


def weekly_expiries(start: date, weeks: int) -> List[date]:
    """Friday expiries (Thursday when Friday is a holiday) after `start`."""
    out = []
    fri = start + timedelta(days=(4 - start.weekday()) % 7 or 7)
    for _ in range(weeks):
        d = fri
        while not is_trading_day(d):
            d -= timedelta(days=1)
        out.append(d)
        fri += timedelta(weeks=1)
    return out


def is_monthly(d: date) -> bool:
    third = _nth_weekday(d.year, d.month, 4, 3)
    return d == third or (d == third - timedelta(days=1) and not is_trading_day(third))


def main() -> None:
    ap = argparse.ArgumentParser(description="NYSE trading-day calendar")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("n", type=int)
    a.add_argument("--from", dest="start", default=date.today().isoformat())
    c = sub.add_parser("count")
    c.add_argument("start")
    c.add_argument("end")
    e = sub.add_parser("expiries")
    e.add_argument("--from", dest="start", default=date.today().isoformat())
    e.add_argument("--weeks", type=int, default=10)
    args = ap.parse_args()

    start = date.fromisoformat(args.start)
    if args.cmd == "add":
        d = add_trading_days(start, args.n)
        print(f"Bar {args.n} after {start:%a %d %b %Y} is {d:%a %d %b %Y}")
    elif args.cmd == "count":
        end = date.fromisoformat(args.end)
        print(f"{count_trading_days(start, end)} trading days after {start:%a %d %b %Y} up to {end:%a %d %b %Y}")
    else:
        hs = sorted(x for y in (start.year, start.year + 1) for x in holidays(y) if start < x)
        print(f"Expiries after {start:%a %d %b %Y} (trading days from {start:%d %b} in brackets)")
        for d in weekly_expiries(start, args.weeks):
            tag = "  monthly" if is_monthly(d) else ""
            print(f"  {d:%a %d %b %Y}  [{count_trading_days(start, d):>3}]{tag}")
        print("Market holidays ahead: " + ", ".join(f"{x:%a %d %b}" for x in hs[:6]))


if __name__ == "__main__":
    main()
