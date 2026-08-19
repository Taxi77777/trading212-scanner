from __future__ import annotations

import run_forex_v4 as v4

scanner = v4.scanner


def flexible_trend(d):
    if not d or len(d.close) < 205:
        return 0
    e20 = scanner.ema(d.close, 20)
    e50 = scanner.ema(d.close, 50)
    e200 = scanner.ema(d.close, 200)
    p = d.close[-1]
    # Full alignment gets the strongest score; partial structure still carries direction.
    if p > e20[-1] > e50[-1] > e200[-1]:
        return 2
    if p > e200[-1] and e20[-1] > e50[-1]:
        return 1
    if p > e200[-1]:
        return 1
    if p < e20[-1] < e50[-1] < e200[-1]:
        return -2
    if p < e200[-1] and e20[-1] < e50[-1]:
        return -1
    if p < e200[-1]:
        return -1
    return 0


# Keep the v3 definition reachable so the two can be compared on identical
# bars instead of being argued about.
strict_trend = scanner.trend

scanner.trend = flexible_trend
scanner.SETUP_MIN = 48
scanner.FINAL_MIN = 68

if __name__ == "__main__":
    raise SystemExit(scanner.main())
