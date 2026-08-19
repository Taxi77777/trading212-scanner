from __future__ import annotations

"""Forex preflight diagnostic.

Answers one question before the scanner runs: *is the pipeline able to produce
a signal at all right now?* It distinguishes a data problem from a legitimately
quiet market, and reports to both stdout (CI logs) and Telegram.

It never sends a trade signal and never changes a threshold.
"""

import sys
from concurrent.futures import ThreadPoolExecutor

import forex_ai_judge
import run_forex_v7 as v7
import telegram_signals as tg

scanner = v7.scanner
PAIRS = list(scanner.PAIRS)

TIMEFRAMES = (
    ("d1", "1d", "2y"),
    ("h1", "1h", "6mo"),
    ("m15", "15m", "10d"),
)


def collect() -> tuple[dict[str, int], dict[str, list[str]]]:
    counts = {"d1": 0, "h1": 0, "h4": 0, "m15": 0}
    failures: dict[str, list[str]] = {"d1": [], "h1": [], "h4": [], "m15": []}
    frames: dict[str, dict[str, object]] = {sym: {} for sym in PAIRS}

    jobs = []
    with ThreadPoolExecutor(max_workers=12) as pool:
        for symbol in PAIRS:
            for key, interval, range_ in TIMEFRAMES:
                jobs.append((symbol, key, pool.submit(scanner.fetch, symbol, interval, range_)))
        for symbol, key, future in jobs:
            try:
                bars = future.result()
            except Exception:
                bars = None
            frames[symbol][key] = bars
            if bars is None:
                failures[key].append(symbol)
            else:
                counts[key] += 1

    for symbol in PAIRS:
        h4 = scanner.resample_h4(frames[symbol].get("h1"))
        if h4 is None:
            failures["h4"].append(symbol)
        else:
            counts["h4"] += 1
    return counts, failures


def main() -> int:
    counts, failures = collect()

    try:
        events = scanner.calendar_events()
    except Exception as exc:
        events = []
        print(f"Calendrier indisponible: {exc}")

    news_blocked = 0
    news_errors = 0
    for symbol in PAIRS:
        try:
            _, blocked = scanner.event_risk(symbol, events)
            news_blocked += int(blocked)
        except Exception as exc:
            news_errors += 1
            print(f"event_risk({symbol}) a échoué: {exc}")

    if forex_ai_judge.configured():
        report = forex_ai_judge.check_connectivity()
        if report["connected"]:
            ai_line = f"CONNECTÉ ({report['model']})"
        else:
            ai_line = f"ERREUR — HTTP {report['http']} — {report['error'][:90]}"
    else:
        report = None
        ai_line = "NON CONFIGURÉE (secrets absents)"

    total = len(PAIRS)
    all_failures = sorted({s for group in failures.values() for s in group})

    lines = [
        "🧪 FOREX PREFLIGHT",
        f"Paires : {total}/{total}",
        f"Données D1 : {counts['d1']}/{total}",
        f"Données H1 : {counts['h1']}/{total}",
        f"H4 construits : {counts['h4']}/{total}",
        f"Données M15 : {counts['m15']}/{total}",
        f"Calendrier chargé : {len(events)} événements",
        f"News high impact bloquées : {news_blocked}",
        f"Erreurs calcul news : {news_errors}",
        f"Session UTC : {v7.session_name_asia_aware()}",
        f"Seuil SETUP : {scanner.SETUP_MIN}",
        f"Seuil ENTRY : {scanner.FINAL_MIN}",
        f"Cooldown : {scanner.COOLDOWN} min | Alertes max : {scanner.MAX_ALERTS}",
        f"Market data : {v7.MARKET_DATA_SOURCE}",
        f"Cloudflare AI : {ai_line}",
        f"Échecs data : {', '.join(all_failures[:10]) if all_failures else 'aucun'}",
    ]
    for key, label in (("d1", "D1"), ("h1", "H1"), ("h4", "H4"), ("m15", "M15")):
        if failures[key]:
            lines.append(f"  · {label} manquants : {', '.join(failures[key][:8])}")

    message = "\n".join(lines)
    print(message)
    tg.telegram_send(message)

    # A preflight failure must not abort the pipeline: a missing pair is a data
    # issue, not a reason to skip the whole scan.
    blocking = counts["d1"] == 0 or counts["h1"] == 0 or counts["m15"] == 0
    if blocking:
        print("PREFLIGHT: aucune donnée exploitable — le scanner ne produira aucun signal.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
