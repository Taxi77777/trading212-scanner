"""Daily + 1H production wrapper: expanded universe, Daily alerts, 1H timing."""
from __future__ import annotations

import daily_1h_longterm_scanner as scanner
from expanded_universe import EXPANDED_SYMBOLS

scanner.MACRO = ["SPY", "QQQ", "^VIX", "UUP", "TLT"]
scanner.FINAL_MIN = 64
scanner.base.SYMBOLS = list(dict.fromkeys(["XYZ" if s == "SQ" else s for s in EXPANDED_SYMBOLS]))
scanner.NAMES["XYZ"] = "Block, Inc."
scanner.NAMES.pop("SQ", None)

_original_hourly_trigger = scanner.hourly_trigger
_original_format_signal = scanner.format_signal
TRIGGER_STATUS: dict[str, bool] = {}


def hourly_trigger(symbol, d, master):
    """Daily remains the signal gate; 1H only controls entry timing."""
    ok = _original_hourly_trigger(symbol, d, master)
    if not ok and d and len(d.close) >= 60:
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
    TRIGGER_STATUS[symbol] = bool(ok)
    # Do not block a valid Daily investment setup when the 1H entry trigger is absent.
    return True


def format_signal(sig, regime, br, inst_label, inst_score, name):
    text = _original_format_signal(sig, regime, br, inst_label, inst_score, name)
    if not TRIGGER_STATUS.get(sig.symbol, False):
        text = text.replace("Déclencheur 1H: 1H_CONFIRMÉ", "Déclencheur 1H: EN ATTENTE — Daily validé")
    return text


scanner.hourly_trigger = hourly_trigger
scanner.format_signal = format_signal

if __name__ == "__main__":
    raise SystemExit(scanner.main())
