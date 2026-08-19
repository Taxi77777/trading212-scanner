from __future__ import annotations

"""Forex v7 production runner.

Pipeline (the AI is a reviewer, never a generator)::

    MOTEUR QUANTITATIF (v3 + v4 taux + v5 tendance + v6/v7 seuils)
        -> SETUP / ENTREE
        -> COHERENCE MULTI-FACTEURS
        -> CLOUDFLARE QWEN (seconde opinion)
        -> CONFIRME / PRUDENCE / CONTRADICTION / INDISPONIBLE
        -> FILTRAGE
        -> MEDAILLE OR / ARGENT / BRONZE
        -> TELEGRAM

Design notes
------------
* The AI runs on the *shortlist*, after the engine and after the cooldown
  check — never on every pair. It can only downgrade or veto, never create.
* A real ``CONTRADICTION`` blocks the alert. ``INDISPONIBLE`` (offline, HTTP
  error, malformed answer) never blocks; Telegram says so explicitly.
* The previous binary DXY veto and the side-agnostic "corrélation CONTRE" veto
  are gone. They rejected valid trades (a USD/CHF BUY with a bearish DXY can be
  perfectly coherent) and were the reason nearly every alert was a CHF cross.
  Coherence is now scored across every available factor in ``forex_quality``.
"""

import json
import os
import time
from datetime import datetime, timezone

import run_forex_v6 as v6
import free_market_data
import forex_ai_judge
import forex_quality

scanner = v6.scanner
base = v6.base if hasattr(v6, "base") else scanner.base

scanner.SETUP_MIN = int(os.getenv("FOREX_SETUP_MIN", "30"))
scanner.FINAL_MIN = int(os.getenv("FOREX_FINAL_MIN", "68"))

MARKET_DATA_SOURCE = free_market_data.SOURCE_NAME
AI_SOURCE = forex_ai_judge.SOURCE_NAME
AI_MAX_CALLS = int(os.getenv("FOREX_AI_MAX_CALLS", str(2 * scanner.MAX_ALERTS)))
AI_FAILURES_BEFORE_OFFLINE = int(os.getenv("FOREX_AI_FAILURES_OFFLINE", "2"))

STATS: dict[str, int] = {}


def _bump(key: str, n: int = 1) -> None:
    STATS[key] = STATS.get(key, 0) + n


# --------------------------------------------------------------------------- #
# Session: the FX market trades around the clock, so Asia is a real session.
# --------------------------------------------------------------------------- #
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


# --------------------------------------------------------------------------- #
# Instrumentation only — no behaviour change.
# --------------------------------------------------------------------------- #
_fetch_orig = scanner.fetch


def fetch_probe(symbol, interval, range_):
    _bump("fetch_calls")
    data = _fetch_orig(symbol, interval, range_)
    _bump("fetch_none" if data is None else "fetch_ok")
    return data


scanner.fetch = fetch_probe


# --------------------------------------------------------------------------- #
# Shortlist -> coherence -> AI -> medals
# --------------------------------------------------------------------------- #
def _cooldown_lookup():
    try:
        state = base.load_state()
    except Exception:
        state = {}
    now = time.time()

    def on_cooldown(sig) -> bool:
        key = f"FXV3:{sig.pair}:{sig.side}:{sig.state}"
        try:
            sent_at = float(state.get(key, {}).get("sent_at", 0))
        except (AttributeError, TypeError, ValueError):
            sent_at = 0.0
        return (now - sent_at) < scanner.COOLDOWN * 60

    return on_cooldown


def _set_ai(sig, result: dict) -> None:
    sig.ai_verdict = result.get("verdict", "INDISPONIBLE")
    sig.ai_confidence = result.get("confidence", 0)
    sig.ai_reason = result.get("reason", "")
    sig.ai_available = bool(result.get("available"))


_NOT_CALLED = {
    "available": False,
    "verdict": "INDISPONIBLE",
    "confidence": 0,
    "contradiction": False,
    "reason": "Hors budget de revue IA pour ce scan",
}


def rank_candidates(candidates: list) -> list:
    """Coherence gate, AI second opinion, quality ranking and medals.

    Only signals that were actually reviewed — or whose reviewer is genuinely
    unreachable — may be returned. A signal is never promoted into an alert slot
    merely because the AI vetoed the one above it.
    """
    survivors: list[tuple[object, dict]] = []

    for sig in candidates:
        coherence = forex_quality.coherence(sig)
        sig.coherence_label = f"{coherence['verdict']} ({coherence['score']}/100)"
        sig.coherence_detail = coherence
        _bump(f"coherence_{coherence['verdict']}")
        if coherence["verdict"] == forex_quality.INCOHERENT:
            scanner.diag_note(sig.symbol, "incoherence_multi_facteurs")
            continue
        survivors.append((sig, coherence))

    # Provisional order (engine score, then coherence) decides review priority.
    survivors.sort(key=lambda t: (t[0].score, t[1]["score"]), reverse=True)

    on_cooldown = _cooldown_lookup()
    ai_configured = forex_ai_judge.configured()
    ai_down = not ai_configured
    calls = 0
    consecutive_failures = 0
    cleared: list = []

    for sig, coherence in survivors:
        if on_cooldown(sig):
            # Skipped before any AI call: reviewing a signal we cannot send is
            # pure waste of the Cloudflare quota.
            scanner.diag_note(sig.symbol, "cooldown")
            _bump("cooldown_skipped")
            continue

        if len(cleared) >= scanner.MAX_ALERTS:
            scanner.diag_note(sig.symbol, "hors_quota_alertes")
            continue

        if ai_down:
            reason = ("Secrets Cloudflare AI absents" if not ai_configured
                      else "Cloudflare AI injoignable sur ce scan")
            _set_ai(sig, dict(_NOT_CALLED, reason=reason))
            _bump("ai_INDISPONIBLE")
        elif calls >= AI_MAX_CALLS:
            # Budget spent. Sending an unreviewed signal would silently bypass
            # the second opinion, so the candidate is dropped instead.
            scanner.diag_note(sig.symbol, "hors_budget_ia")
            continue
        else:
            calls += 1
            verdict = forex_ai_judge.judge_signal(sig)
            _set_ai(sig, verdict)
            _bump(f"ai_{verdict.get('verdict', 'INDISPONIBLE')}")
            if not verdict.get("available"):
                consecutive_failures += 1
                if consecutive_failures >= AI_FAILURES_BEFORE_OFFLINE:
                    # An outage is not a contradiction: stop calling, keep going.
                    ai_down = True
            else:
                consecutive_failures = 0
                if verdict.get("contradiction"):
                    scanner.diag_note(sig.symbol, "contradiction_ia")
                    continue

        assessment = forex_quality.quality(sig, coherence)
        sig.quality_score = assessment["quality"]
        sig.quality_detail = assessment
        cleared.append(sig)

    cleared.sort(key=lambda s: (s.quality_score, s.score), reverse=True)

    # Medal = rank AND absolute quality. A weak best-of-a-bad-batch signal is
    # never promoted to gold just for finishing first.
    order = {"OR": 3, "ARGENT": 2, "BRONZE": 1, "": 0}
    for index, sig in enumerate(cleared):
        rank_emoji, rank_name = forex_quality.medal({0: 100, 1: 70, 2: 58}.get(index, 0))
        abs_emoji, abs_name = forex_quality.medal(sig.quality_score)
        emoji, name = (rank_emoji, rank_name) if order[rank_name] <= order[abs_name] \
            else (abs_emoji, abs_name)
        sig.medal_label = f"{emoji} {name}".strip()
        if name:
            _bump(f"medal_{name}")

    return cleared


scanner.rank_candidates = rank_candidates


# --------------------------------------------------------------------------- #
# Diagnostic report
# --------------------------------------------------------------------------- #
def diagnostic_message(ai_report: dict | None = None) -> str:
    run = getattr(scanner, "LAST_RUN", {}) or {}
    diag = run.get("diag", {})
    ai_state = "NON CONFIGURÉE"
    if ai_report is not None:
        ai_state = "CONNECTÉE" if ai_report.get("connected") else f"ERREUR ({ai_report.get('error', '')[:80]})"
    elif forex_ai_judge.configured():
        ai_state = "CONFIGURÉE"

    def d(key: str) -> int:
        return int(diag.get(key, 0))

    data_failures = sum(v for k, v in diag.items() if k.startswith("donnees_insuffisantes"))
    lines = [
        "🔎 DIAGNOSTIC FOREX V7",
        f"Session : {scanner.session_name()}",
        f"Market data : {MARKET_DATA_SOURCE}",
        f"IA secondaire : {AI_SOURCE}",
        f"Cloudflare AI : {ai_state}",
        f"Paires analysées : {run.get('pairs', 0)}",
        f"fetch OK : {STATS.get('fetch_ok', 0)} / {STATS.get('fetch_calls', 0)} (échecs {STATS.get('fetch_none', 0)})",
        f"Calendrier : {run.get('calendar_events', 0)} événements",
        f"Seuil SETUP : {scanner.SETUP_MIN} | Seuil ENTREE : {scanner.FINAL_MIN}",
        f"SETUP retenus : {run.get('setups', 0)} | dont ENTREE : {run.get('entries', 0)}",
        f"Envoyés Telegram : {run.get('sent', 0)}",
        "— Motifs de rejet —",
        f"Données insuffisantes : {data_failures}",
        f"Aucune direction nette : {d('aucune_direction')}",
        f"Score sous le seuil SETUP : {d('score_sous_seuil_setup')}",
        f"Hors session : {d('hors_session')}",
        f"News high impact bloquante : {d('news_bloquante')}",
        f"Incohérence multi-facteurs : {d('incoherence_multi_facteurs')}",
        f"Contradiction IA : {d('contradiction_ia')}",
        f"Cooldown actif : {d('cooldown')}",
        f"Hors budget de revue IA : {d('hors_budget_ia')}",
        f"Hors quota d'alertes ({scanner.MAX_ALERTS}) : {d('hors_quota_alertes')}",
        f"Échec envoi Telegram : {d('echec_envoi_telegram')}",
        "— IA —",
        f"CONFIRME {STATS.get('ai_CONFIRME', 0)} | PRUDENCE {STATS.get('ai_PRUDENCE', 0)} | "
        f"CONTRADICTION {STATS.get('ai_CONTRADICTION', 0)} | INDISPONIBLE {STATS.get('ai_INDISPONIBLE', 0)}",
        f"Médailles : 🥇 {STATS.get('medal_OR', 0)} | 🥈 {STATS.get('medal_ARGENT', 0)} | 🥉 {STATS.get('medal_BRONZE', 0)}",
        f"Macro : {run.get('macro', 'n/d')} — {run.get('macro_reason', '')}",
        "Diagnostic du moteur réel — aucun score artificiel, aucun seuil abaissé.",
    ]
    return "\n".join(lines)


def main() -> int:
    STATS.clear()
    # The V7 diagnostic supersedes the v3 one-liner; sending both is noise.
    scanner.SUPPRESS_SUMMARY = True
    report = forex_ai_judge.check_connectivity() if forex_ai_judge.configured() else None
    if report is not None:
        print(
            "Cloudflare AI : "
            + ("CONNECTÉ" if report.get("connected") else "ERREUR")
            + f" | Modèle : {report.get('model')}"
            + (f" | HTTP : {report.get('http')}" if not report.get("connected") else "")
            + (f" | Cause : {report.get('error')}" if not report.get("connected") else "")
        )
    rc = scanner.main()
    message = diagnostic_message(report)
    print(message)
    try:
        base.telegram_send(message)
    except Exception as exc:  # diagnostics must never break the run
        print(f"Diagnostic Telegram non envoyé: {exc}")
    print("DIAG_JSON " + json.dumps(getattr(scanner, "LAST_RUN", {}).get("diag", {}),
                                    ensure_ascii=False, sort_keys=True))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
