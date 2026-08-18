"""Daily + 1H production wrapper: expanded universe, relaxed timing, diagnostics."""
from __future__ import annotations

import daily_1h_longterm_scanner as scanner
from expanded_universe import EXPANDED_SYMBOLS

scanner.MACRO = ["SPY", "QQQ", "^VIX", "UUP", "TLT"]
scanner.FINAL_MIN = 64
scanner.base.SYMBOLS = list(dict.fromkeys(["XYZ" if s == "SQ" else s for s in EXPANDED_SYMBOLS]))
scanner.NAMES["XYZ"] = "Block, Inc."
scanner.NAMES.pop("SQ", None)

_original_hourly_trigger = scanner.hourly_trigger
_original_daily_master = scanner.daily_master
_original_telegram_send = scanner.base.telegram_send

DIAG = {"daily_master": 0, "daily_candidate": 0, "hourly_confirm": 0}


def daily_master(symbol, d, spy):
    sig = _original_daily_master(symbol, d, spy)
    if sig is not None:
        DIAG["daily_master"] += 1
        if sig.score >= 58:
            DIAG["daily_candidate"] += 1
    return sig


def hourly_trigger(symbol, d, master):
    if _original_hourly_trigger(symbol, d, master):
        DIAG["hourly_confirm"] += 1
        return True
    if not d or len(d.close) < 60:
        return False
    p = d.close[-1]
    e20 = scanner.ema(d.close, 20)
    volavg = scanner.mean(d.volume[-21:-1]) if len(d.volume) >= 22 else 0.0
    vr = d.volume[-1] / volavg if volavg else 0.0
    recent_high = max(d.high[-7:-1])
    recent_low = min(d.low[-7:-1])
    if master.side == "BUY":
        trend = p > e20[-1] and e20[-1] >= e20[-5]
        continuation = p >= recent_high or (d.close[-1] > d.close[-2] > d.close[-3])
        ok = trend and continuation and vr >= 0.65
    else:
        trend = p < e20[-1] and e20[-1] <= e20[-5]
        continuation = p <= recent_low or (d.close[-1] < d.close[-2] < d.close[-3])
        ok = trend and continuation and vr >= 0.65
    if ok:
        DIAG["hourly_confirm"] += 1
    return ok


def telegram_send(text):
    if text.startswith("📈 Scan DAILY + 1H:"):
        text += (
            f"\n🔎 Diagnostic: Daily setups >=58 = {DIAG['daily_candidate']}"
            f" | confirmations 1H = {DIAG['hourly_confirm']}"
            f" | seuil final = {scanner.FINAL_MIN}"
        )
    return _original_telegram_send(text)


scanner.daily_master = daily_master
scanner.hourly_trigger = hourly_trigger
scanner.base.telegram_send = telegram_send

if __name__ == "__main__":
    raise SystemExit(scanner.main())
