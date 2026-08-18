from __future__ import annotations

from datetime import datetime, timezone
import run_forex_v6 as v6

scanner = v6.scanner
scanner.SETUP_MIN = 30
scanner.FINAL_MIN = 68

# Forex is open around the clock on weekdays. The previous engine blocked
# every setup from 21:00-07:00 UTC, which silently disabled the Asian session.
def session_name_asia_aware() -> str:
    now = datetime.now(timezone.utc)
    h = now.hour + now.minute / 60
    if 0 <= h < 7:
        return "ASIE"
    if 7 <= h < 12:
        return "LONDRES"
    if 12 <= h < 17:
        return "LONDRES + NEW YORK"
    if 17 <= h < 21:
        return "NEW YORK"
    return "ASIE"

scanner.session_name = session_name_asia_aware

# The base engine expects a Yahoo symbol key (e.g. EURUSD=X) in event_risk(),
# while its main loop was passing the display label (e.g. EUR/USD).
_event_risk_orig = scanner.event_risk

def event_risk_normalized(pair, events):
    if pair not in scanner.PAIRS:
        pair = next((symbol for symbol, values in scanner.PAIRS.items() if values[2] == pair), pair)
    return _event_risk_orig(pair, events)

scanner.event_risk = event_risk_normalized

_fetch_orig = scanner.fetch
_build_orig = scanner.build_signal
_stats = {"fetch_calls": 0, "fetch_ok": 0, "fetch_none": 0, "build_calls": 0, "build_ok": 0, "build_none": 0, "SETUP": 0, "ENTREE": 0}

def fetch_probe(symbol, interval, range_):
    _stats["fetch_calls"] += 1
    d = _fetch_orig(symbol, interval, range_)
    if d is None:
        _stats["fetch_none"] += 1
    else:
        _stats["fetch_ok"] += 1
    return d

def build_probe(pair, frames, strength, macro, macro_reason, news, news_block):
    _stats["build_calls"] += 1
    sig = _build_orig(pair, frames, strength, macro, macro_reason, news, news_block)
    if sig is None:
        _stats["build_none"] += 1
    else:
        _stats["build_ok"] += 1
        _stats[sig.state] = _stats.get(sig.state, 0) + 1
    return sig

scanner.fetch = fetch_probe
scanner.build_signal = build_probe

if __name__ == "__main__":
    rc = scanner.main()
    msg = (
        "🔎 DIAGNOSTIC FOREX V7\n"
        f"Session : {session_name_asia_aware()}\n"
        f"fetch OK : {_stats['fetch_ok']} / {_stats['fetch_calls']}\n"
        f"fetch KO : {_stats['fetch_none']}\n"
        f"build appels : {_stats['build_calls']}\n"
        f"build retenus : {_stats['build_ok']}\n"
        f"build rejetés : {_stats['build_none']}\n"
        f"SETUP : {_stats.get('SETUP', 0)} | ENTREE : {_stats.get('ENTREE', 0)}\n"
        "Diagnostic du moteur réel — aucun score artificiel ajouté."
    )
    try:
        v6.base.telegram_send(msg)
    except Exception:
        pass
    raise SystemExit(rc)
