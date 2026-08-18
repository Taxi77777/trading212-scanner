"""Daily + 1H production wrapper with expanded universe and robust 1H timing."""
from __future__ import annotations

import daily_1h_longterm_scanner as scanner
from expanded_universe import EXPANDED_SYMBOLS

# Macro inputs must match the five-value unpacking in macro_regime().
scanner.MACRO = ["SPY", "QQQ", "^VIX", "UUP", "TLT"]

# Use the expanded investable universe and repair the legacy Block ticker.
scanner.base.SYMBOLS = list(dict.fromkeys(["XYZ" if s == "SQ" else s for s in EXPANDED_SYMBOLS]))
scanner.NAMES["XYZ"] = "Block, Inc."
scanner.NAMES.pop("SQ", None)

# Keep the Daily score strict; make the 1H trigger a timing confirmation rather than
# requiring a fresh intraday breakout on the exact scan candle.
_original_hourly_trigger = scanner.hourly_trigger

def hourly_trigger(symbol, d, master):
    if _original_hourly_trigger(symbol, d, master):
        return True
    if not d or len(d.close) < 60:
        return False
    p = d.close[-1]
    e20 = scanner.ema(d.close, 20)
    volavg = scanner.mean(d.volume[-21:-1]) if len(d.volume) >= 22 else 0.0
    vr = d.volume[-1] / volavg if volavg else 0.0
    if master.side == "BUY":
        trend = p > e20[-1] and e20[-1] >= e20[-5]
        return trend and vr >= 0.75
    trend = p < e20[-1] and e20[-1] <= e20[-5]
    return trend and vr >= 0.75

scanner.hourly_trigger = hourly_trigger

if __name__ == "__main__":
    raise SystemExit(scanner.main())
