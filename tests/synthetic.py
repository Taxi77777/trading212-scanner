"""Deterministic synthetic OHLC series so the engine can be tested offline."""
import math

import forex_intraday_scanner_v3 as scanner

Bars = scanner.Bars


def series(n: int, start: float, step: float, step_seconds: int,
           wobble: float = 0.0, t0: int = 1_700_000_000) -> Bars:
    ts, o, h, l, c, v = [], [], [], [], [], []
    price = start
    for i in range(n):
        drift = step
        noise = wobble * math.sin(i / 7.0)
        open_ = price
        close = price + drift + noise
        high = max(open_, close) + abs(step) * 0.6 + abs(wobble) * 0.4 + 1e-6
        low = min(open_, close) - abs(step) * 0.6 - abs(wobble) * 0.4 - 1e-6
        ts.append(t0 + i * step_seconds)
        o.append(open_); h.append(high); l.append(low); c.append(close)
        v.append(1000.0 + i)
        price = close
    return Bars(ts, o, h, l, c, v)


def frames(direction: int = 1, m15_bars: int = 400):
    """Build a complete, internally consistent frame set for one pair."""
    step = 0.0004 * direction
    d1 = series(300, 1.0000, step * 3, 86400, wobble=0.0002)
    h1 = series(1200, 1.0000, step * 0.25, 3600, wobble=0.00015)
    m15 = series(m15_bars, 1.0000, step * 0.1, 900, wobble=0.00012)
    h4 = scanner.resample_h4(h1)
    return {"d1": d1, "h4": h4, "h1": h1, "m15": m15}


def market(dxy_direction: int = -1):
    dxy = series(300, 100.0, 0.35 * dxy_direction, 86400)
    tnx = series(300, 4.0, 0.001, 86400)
    vix = series(300, 16.0, -0.005, 86400)
    spy = series(300, 400.0, 0.3, 86400)
    return {scanner.DXY: dxy, scanner.US10Y: tnx, scanner.VIX: vix, scanner.SPY: spy}
