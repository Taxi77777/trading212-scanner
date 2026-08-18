from __future__ import annotations

from datetime import datetime, timezone
import run_forex_v6 as v6
import forex_market_data

scanner = v6.scanner
scanner.SETUP_MIN = 30
scanner.FINAL_MIN = 68

# Completely free, no-key market/reference sources.
MARKET_DATA_SOURCE = "Yahoo Finance intraday + Frankfurter/ECB reference + Forex Factory calendar"

# Forex is open around the clock on weekdays. Keep the Asian/Sydney transition
# active so the scanner does not silently stop overnight UTC.
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

# Normalize display labels (EUR/USD) to Yahoo symbols (EURUSD=X) before
# calling the base calendar-risk function.
_event_risk_orig = scanner.event_risk

def event_risk_normalized(pair, events):
    if pair not in scanner.PAIRS:
        pair = next((symbol for symbol, values in scanner.PAIRS.items() if values[2] == pair), pair)
    label, blocked = _event_risk_orig(pair, events)
    if not blocked and label == "AUCUN HIGH IMPACT CONFIGURE":
        label = "Aucun événement high impact dans les 30 prochaines minutes"
    return label, blocked

scanner.event_risk = event_risk_normalized

# Hard coherence filters. A signal is rejected when the macro/correlation
# layer directly contradicts the traded USD direction.
_build_orig = scanner.build_signal

def build_signal_coherent(pair, frames, strength, macro, macro_reason, news, news_block):
    sig = _build_orig(pair, frames, strength, macro, macro_reason, news, news_block)
    if sig is None:
        return None

    if "USD" in pair:
        usd_is_base = scanner.PAIRS[pair][0] == "USD"
        if usd_is_base and sig.side == "BUY" and sig.dxy == "BEAR":
            return None
        if usd_is_base and sig.side == "SELL" and sig.dxy == "BULL":
            return None
        if not usd_is_base and sig.side == "BUY" and sig.dxy == "BULL":
            return None
        if not usd_is_base and sig.side == "SELL" and sig.dxy == "BEAR":
            return None

    if sig.correlation == "CONTRE":
        return None

    return sig

scanner.build_signal = build_signal_coherent

# Medal ranking is applied to the candidate order used by the production
# engine (already sorted by score before formatting).
_format_orig = scanner.format_signal
_rank = {"n": 0}

def format_signal_medals(sig):
    medal = "🥇 OR" if _rank["n"] == 0 else "🥈 ARGENT" if _rank["n"] == 1 else "🥉 BRONZE" if _rank["n"] == 2 else ""
    _rank["n"] += 1
    text = _format_orig(sig)
    if medal:
        text = text.replace(f"🟢 SIGNAL FOREX {sig.state}", f"🟢 {medal} — SIGNAL FOREX {sig.state}", 1)
        text = text.replace(f"🔴 SIGNAL FOREX {sig.state}", f"🔴 {medal} — SIGNAL FOREX {sig.state}", 1)
    return text

scanner.format_signal = format_signal_medals

_fetch_orig = scanner.fetch
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
    sig = build_signal_coherent(pair, frames, strength, macro, macro_reason, news, news_block)
    if sig is None:
        _stats["build_none"] += 1
    else:
        _stats["build_ok"] += 1
        _stats[sig.state] = _stats.get(sig.state, 0) + 1
    return sig

scanner.fetch = fetch_probe
scanner.build_signal = build_probe

if __name__ == "__main__":
    _rank["n"] = 0
    rc = scanner.main()
    msg = (
        "🔎 DIAGNOSTIC FOREX V7\n"
        f"Market data : {MARKET_DATA_SOURCE}\n"
        f"Session : {session_name_asia_aware()}\n"
        f"fetch OK : {_stats['fetch_ok']} / {_stats['fetch_calls']}\n"
        f"fetch KO : {_stats['fetch_none']}\n"
        f"build appels : {_stats['build_calls']}\n"
        f"build retenus : {_stats['build_ok']}\n"
        f"build rejetés : {_stats['build_none']}\n"
        f"SETUP : {_stats.get('SETUP', 0)} | ENTREE : {_stats.get('ENTREE', 0)}\n"
        "Filtres de cohérence : DXY USD + corrélation CONTRADICTION\n"
        "Diagnostic du moteur réel — aucun score artificiel ajouté."
    )
    try:
        v6.base.telegram_send(msg)
    except Exception:
        pass
    raise SystemExit(rc)
