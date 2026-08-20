"""Mise en forme et envoi des alertes Telegram.

Règles tenues ici :

* aucun secret n'apparaît jamais dans un message, un log ou une erreur ;
* le message dit ce qui est observé, jamais ce qui va se passer — pas de
  « cette action va monter » ;
* l'absence de signal est un message à part entière, pas un silence ;
* le plan est présenté comme une analyse : aucun ordre n'est transmis nulle part.
"""
from __future__ import annotations

import html
import os
from typing import Any

import requests

from . import phases as ph

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
API = "https://api.telegram.org/bot{token}/sendMessage"
TIMEOUT = float(os.getenv("TELEGRAM_TIMEOUT", "20"))
LIMIT = 3800                      # Telegram coupe à 4096 ; on garde une marge

MEDALS = ("🥇", "🥈", "🥉")

DISCLAIMER = ("Analyse statistique, pas un conseil d'investissement. "
              "Aucun ordre n'est transmis à un courtier.")


def configured() -> bool:
    return bool(BOT_TOKEN and CHAT_ID)


def redact(text: object) -> str:
    """Retire jeton et identifiant de discussion d'un texte de diagnostic."""
    out = str(text or "")
    if BOT_TOKEN:
        out = out.replace(BOT_TOKEN, "***")
        head = BOT_TOKEN.split(":")[0]
        if head:
            out = out.replace(head, "***")
    if CHAT_ID:
        out = out.replace(CHAT_ID, "***")
    return out


def esc(text: object) -> str:
    return html.escape(str(text if text is not None else ""), quote=False)


def _num(value: float | None, digits: int = 2) -> str:
    return "—" if value is None else f"{value:,.{digits}f}".replace(",", " ")


# --------------------------------------------------------------------------
# Mise en forme
# --------------------------------------------------------------------------
def format_header(summary: Any) -> str:
    reg = summary.regime
    lines = [f"<b>📊 SCANNER PRÉ-CASSURE — {esc(summary.date)}</b>"]
    if reg is not None:
        bits = [f"Régime <b>{esc(reg.label)}</b> {reg.score:.0f}/100"]
        if summary.regime_label_market:
            bits.append(esc(summary.regime_label_market))
        if reg.breadth_pct is not None:
            bits.append(f"{reg.breadth_pct:.0f} % des valeurs &gt; MM50")
        if reg.vix is not None:
            bits.append(f"VIX {reg.vix:.1f}")
        lines.append(" · ".join(bits))
        for note in reg.notes[:2]:
            lines.append(f"<i>{esc(note)}</i>")
    counts = summary.counts or {}
    detail = " · ".join(f"{counts.get(name, 0)} {ph.PHASE_LABELS[name].lower()}"
                        for name in (ph.PRE_BREAKOUT, ph.BREAKOUT, ph.RETEST, ph.EARLY)
                        if counts.get(name))
    line = f"{summary.analysed} valeurs analysées"
    if detail:
        line += f" · {detail}"
    if summary.failed:
        line += f" · {summary.failed} indisponibles"
    lines.append(line)
    if summary.blocked_by_ai:
        lines.append(f"<i>{summary.blocked_by_ai} écartée(s) par contradiction IA</i>")
    return "\n".join(lines)


def format_no_trade(summary: Any) -> str:
    lines = [format_header(summary), "",
             "<b>⛔ Aucun signal aujourd'hui.</b>",
             "Aucune configuration ne remplit les critères. "
             "Ne rien faire est une décision, pas un échec du scanner.", ""]
    if summary.regime is not None and not summary.regime.allow_new_positions:
        lines.append("Le régime de marché interdit toute nouvelle prise de risque.")
        lines.append("")
    lines.append(f"<i>{esc(DISCLAIMER)}</i>")
    return "\n".join(lines)


def format_candidate(c: Any, rank: int | None = None) -> str:
    medal = MEDALS[rank] if rank is not None and rank < len(MEDALS) else ""
    head = f"{c.phase.emoji} <b>{esc(c.ticker)}</b>"
    if medal:
        head = f"{medal} {head}"
    if c.market_label:
        head += f" <i>({esc(c.market_label)})</i>"

    lines = [head,
             f"<b>{esc(c.phase.label)}</b> · Score {c.score.total:.0f}/100 "
             f"({esc(c.score.grade)}) · Pré-cassure {c.score.prebreakout:.0f}/100",
             f"Cours {_num(c.price)} {esc(c.currency)}"]

    if c.phase.reasons:
        lines.append("")
        lines += [f"• {esc(r)}" for r in c.phase.reasons[:4]]

    lines.append("")
    lines.append("<b>Structure</b>")
    lines.append(f"Tendance : {esc(c.trend.direction)} ({c.trend.score:.0f}/10)")
    if c.base.found:
        lines.append(f"Base : {esc(c.base.label)}, {c.base.length} séances, "
                     f"amplitude {c.base.width_pct:.1f} %")
    if c.resistance is not None:
        lines.append(f"Résistance : {_num(c.resistance.level)} "
                     f"(testée {c.resistance.tests}×, à {c.distance_pct:+.1f} %)")
    if c.rs.available:
        lines.append(f"Force relative : {c.rs.rs_3m:+.1f} pts sur 3 mois "
                     f"vs {esc(c.benchmark_label)}")
    for note in (c.volume.notes[:1] + c.accum.notes[:1] + c.comp.notes[:1]):
        lines.append(f"· {esc(note)}")

    if c.fundamental is not None and getattr(c.fundamental, "available", False):
        lines.append("")
        lines.append(f"<b>Fondamentaux</b> {c.fundamental.score:.1f}/10")
        for note in (c.fundamental.notes or [])[:3]:
            lines.append(f"· {esc(note)}")

    lines.append("")
    if c.plan.tradeable:
        lines.append("<b>Plan (analyse — aucun ordre transmis)</b>")
        lines.append(f"Entrée : {_num(c.plan.entry)} — {esc(c.plan.entry_kind)}")
        lines.append(f"Invalidation : {_num(c.plan.stop)} (−{c.plan.risk_pct:.1f} %)")
        lines.append(" · ".join(f"{esc(t.label)} {_num(t.price)} ({t.r_multiple:.1f}R)"
                                for t in c.plan.targets))
        lines.append("Objectifs = hauteur de la base reportée depuis le pivot")
        lines.append(f"Taille indicative : {c.plan.position_pct:.1f} % du capital "
                     "pour 1 % de risque")
    else:
        reason = c.plan.notes[0] if c.plan.notes else "structure insuffisante"
        lines.append(f"<b>Pas de plan exploitable</b> — {esc(reason)}")

    if c.ai:
        lines.append("")
        if c.ai.get("available"):
            lines.append(f"<b>IA (2ᵉ avis)</b> : {esc(c.ai.get('verdict'))} "
                         f"{c.ai.get('confidence', 0)}% — {esc(c.ai.get('reason'))}")
        else:
            lines.append(f"<b>IA (2ᵉ avis)</b> : indisponible — "
                         f"{esc(c.ai.get('reason', 'non contacté'))}")

    detail = " · ".join(f"{esc(comp.label)} {comp.points:.0f}/{comp.weight:.0f}"
                        for comp in c.score.components)
    if detail:
        lines.append("")
        lines.append(f"<i>{detail}</i>")
    for name, value in c.score.penalties:
        lines.append(f"<i>Pénalité {esc(name)} −{value:.0f}</i>")
    return "\n".join(lines)


def build_report(summary: Any, candidates: list[Any]) -> list[str]:
    """Découpe le rapport en messages Telegram, sans jamais couper un candidat."""
    if not candidates:
        return [format_no_trade(summary)]
    blocks = [format_header(summary)]
    blocks += [format_candidate(c, rank=i) for i, c in enumerate(candidates)]
    blocks.append(f"<i>{esc(DISCLAIMER)}</i>")

    messages: list[str] = []
    current = ""
    for block in blocks:
        block = block[:LIMIT]
        if not current:
            current = block
        elif len(current) + len(block) + 2 <= LIMIT:
            current += "\n\n" + block
        else:
            messages.append(current)
            current = block
    if current:
        messages.append(current)
    return messages


# --------------------------------------------------------------------------
# Envoi
# --------------------------------------------------------------------------
def send(text: str, *, dry_run: bool = False) -> dict[str, Any]:
    if dry_run:
        return {"ok": True, "http": None, "error": "", "dry_run": True}
    if not configured():
        return {"ok": False, "http": None, "error": "Secrets Telegram absents"}
    try:
        response = requests.post(
            API.format(token=BOT_TOKEN),
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=TIMEOUT)
    except Exception as exc:
        return {"ok": False, "http": None, "error": redact(exc)[:200]}
    if response.status_code != 200:
        return {"ok": False, "http": response.status_code,
                "error": redact(response.text)[:200]}
    return {"ok": True, "http": 200, "error": ""}


def send_report(summary: Any, candidates: list[Any], *,
                dry_run: bool = False) -> dict[str, Any]:
    messages = build_report(summary, candidates)
    sent, errors = 0, []
    for message in messages:
        result = send(message, dry_run=dry_run)
        if result["ok"]:
            sent += 1
        else:
            errors.append(result["error"])
    return {"ok": sent == len(messages), "sent": sent,
            "total": len(messages), "errors": errors}
