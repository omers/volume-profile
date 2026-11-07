"""Write deterministic synthetic daily bars (no network) for the CI smoke test."""
from __future__ import annotations

import math
import sys
from datetime import date, timedelta


def main(path: str, n: int = 160) -> None:
    d = date(2026, 1, 1)
    price = 500.0
    rows = ["date,open,high,low,close,volume"]
    i = 0
    while len(rows) <= n:
        if d.weekday() < 5:
            o = price
            c = o + 4 * math.sin(i / 6) + 1.5 * math.sin(i / 2.3)
            h = max(o, c) + 1.2 + abs(math.sin(i))
            lo = min(o, c) - 1.2 - abs(math.cos(i))
            v = 80_000_000 + 20_000_000 * math.sin(i / 4)
            rows.append(f"{d.isoformat()},{o:.2f},{h:.2f},{lo:.2f},{c:.2f},{v:.0f}")
            price = c
            i += 1
        d += timedelta(days=1)
    with open(path, "w") as f:
        f.write("\n".join(rows) + "\n")


if __name__ == "__main__":
    main(sys.argv[1])
