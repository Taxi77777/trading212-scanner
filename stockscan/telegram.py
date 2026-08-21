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
from . import watchlist as wl

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
API = "https://api.telegram.org/bot{token}/sendMessage"
TIMEOUT = float(os.getenv("TELEGRAM_TIMEOUT", "").strip() or "20")
LIMIT = 3800                      # Telegram coupe à 4096 ; on garde une marge

MEDALS = ("🥇", "🥈", "🥉")

DISCLAIMER = ("Analyse statistique, pas un conseil d'investissement. "
              "Aucun ordre n'est transmis à un courtier.\n"
              "Sur la durée, environ 1 signal sur 3 gagne — mais les gains sont "
              "plus gros que les pertes. L'avantage est réel et mince : il ne "
              "se voit que sur des dizaines de trades, jamais sur un seul.")

LEGENDE = ("<b>Comment lire</b>\n"
           "L'ordre 🥇🥈🥉 est celui du système ; il n'a pas été prouvé qu'un mieux classé finit mieux.\n"
           "« Sortie de secours » = le prix qui prouve que l'analyse est fausse. "
           "S'y tenir est ce qui protège le capital.\n"
           "L'objectif est un niveau lu sur le graphique, pas une prévision.")


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


def _prix(value: float | None, currency: str = "") -> str:
    """Prix lisible, avec la conversion quand la cotation trompe.

    Londres cote en PENCE : « 1295 GBP » se lit comme mille deux cent
    quatre-vingt-quinze livres alors qu'il s'agit de 12,95 £. Un facteur cent
    sur un message d'aide a la decision n'est pas une coquette imprecision.
    """
    if value is None:
        return "—"
    if currency == "GBp":
        return f"{_num(value)} pence (soit {_num(value / 100)} £)"
    return f"{_num(value)} {currency}".strip()


# --------------------------------------------------------------------------
# Mise en forme
# --------------------------------------------------------------------------
REGIME_MOT = {"RISK_ON": "porteur", "NEUTRE": "hésitant", "RISK_OFF": "difficile"}


def format_header(summary: Any) -> str:
    """L'etat du marche en francais, pas en indicateurs.

    « Régime RISK_ON 79/100 · 10 % des valeurs > MM50 · VIX 16.0 » ne dit rien
    a qui n'est pas analyste. Le meme fait, dit simplement, se retient.
    """
    reg = summary.regime
    lines = [f"<b>📊 SCANNER ACTIONS — {esc(summary.date)}</b>"]
    if reg is not None:
        mot = REGIME_MOT.get(reg.label, reg.label.lower())
        ligne = f"Marché <b>{esc(mot)}</b>"
        if reg.breadth_pct is not None:
            ligne += f" — {reg.breadth_pct:.0f} % des actions bien orientées"
        lines.append(ligne)
        if not reg.allow_new_positions:
            lines.append("<b>Marché trop dégradé : aucune prise de risque conseillée.</b>")

    counts = summary.counts or {}
    retenues = summary.kept
    mot = "signaux" if retenues > 1 else "signal"
    lines.append(f"{summary.analysed} valeurs passées au crible · "
                 f"<b>{retenues} {mot}</b>")
    if counts.get(ph.NO_TRADE):
        lines.append(f"<i>{counts[ph.NO_TRADE]} valeurs écartées : rien à y faire "
                     "aujourd'hui</i>")
    if summary.blocked_by_ai:
        lines.append(f"<i>{summary.blocked_by_ai} écartée(s) par l'IA</i>")
    dropped = getattr(summary, "dropped_correlated", 0)
    if dropped:
        lines.append(f"<i>{dropped} écartée(s) : trop ressemblante(s) à une autre "
                     "déjà retenue</i>")
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


# Traduction en francais courant de ce que le backtest a mesure. L'utilisateur
# n'est pas analyste : il doit voir en un mot si le signal repose sur une mesure
# ou sur une hypothese, sans avoir a interpreter un R-multiple.
PHASE_PREUVE = {
    "RETEST": "✅ Type de signal déjà validé",
    "BREAKOUT": "🟡 Type de signal moyennement fiable",
    "PRE_BREAKOUT": "❓ Type de signal pas encore prouvé",
    "EARLY": "❓ Type de signal jamais testé",
}


def format_candidate(c: Any, rank: int | None = None) -> str:
    """Six lignes, du francais courant, aucun jargon.

    La version precedente ouvrait sur le score, la tendance, la structure et la
    force relative : vingt lignes avant d'arriver au prix. Un message qu'on ne
    peut pas lire en trois secondes n'est pas une alerte, c'est un rapport - et
    un rapport qu'on ne comprend pas ne sert a rien. Ici on garde uniquement ce
    qui permet de decider : a quel prix, ou est la sortie, combien engager.
    """
    medal = MEDALS[rank] if rank is not None and rank < len(MEDALS) else "▪️"
    nom = getattr(c, "name", "") or c.ticker
    titre = f"{medal} <b>{esc(nom)}</b>"
    if nom != c.ticker:
        titre += f" <i>({esc(c.ticker)} · {esc(c.market_label)})</i>"
    else:
        titre += f" <i>({esc(c.market_label)})</i>"
    lines = [titre,
             f"Cours actuel : <b>{_prix(c.price, c.currency)}</b>",
             PHASE_PREUVE.get(c.phase.name, ""),
             ""]

    if c.plan.tradeable:
        cible = c.plan.targets[0] if c.plan.targets else None
        lines.append(f"Entrée si le cours dépasse <b>{_prix(c.plan.entry, c.currency)}</b>")
        lines.append(f"Sortie de secours à <b>{_prix(c.plan.stop, c.currency)}</b> "
                     f"→ tu perds {c.plan.risk_pct:.1f} %")
        if cible:
            lines.append(f"Objectif <b>{_prix(cible.price, c.currency)}</b> "
                         f"→ tu gagnes {cible.r_multiple:.1f} fois ce que tu risques")
        lines.append(f"Ne pas engager plus de <b>{c.plan.position_pct:.0f} %</b> "
                     "de ton capital")
    else:
        reason = c.plan.notes[0] if c.plan.notes else "structure insuffisante"
        lines.append(f"<i>Pas de plan exploitable — {esc(reason)}</i>")

    if c.ai and c.ai.get("available") and c.ai.get("verdict") == "PRUDENCE":
        lines.append("")
        lines.append(f"<i>⚠️ {esc(c.ai.get('reason'))}</i>")
    return "\n".join(lines)


def build_report(summary: Any, candidates: list[Any]) -> list[str]:
    """Découpe le rapport en messages Telegram, sans jamais couper un candidat."""
    if not candidates:
        return [format_no_trade(summary)]
    blocks = [format_header(summary), LEGENDE]
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


# --------------------------------------------------------------------------
# Alertes de surveillance intraséance
# --------------------------------------------------------------------------
def format_event(event: Any) -> str:
    """Court, net, actionnable. Une alerte se lit en marchant."""
    w = event.watch
    titre = wl.EVENT_LABEL.get(event.kind, event.kind)
    nom = w.name or w.ticker
    lines = [f"<b>{esc(titre)}</b>",
             f"<b>{esc(nom)}</b> <i>({esc(w.ticker)} · {esc(w.market_label)})</i>",
             f"Cours : <b>{_prix(event.price, w.currency)}</b>"]
    for note in event.notes[:2]:
        lines.append(esc(note))

    if event.kind == wl.DECLENCHE:
        lines.append("")
        lines.append(f"Sortie de secours : <b>{_prix(w.stop, w.currency)}</b>")
        lines.append(f"Objectif : <b>{_prix(w.target, w.currency)}</b>")
        lines.append("<i>Le plan est enclenché. La sortie de secours est ce qui "
                     "protège le capital.</i>")
    elif event.kind == wl.STOPPE:
        lines.append("")
        lines.append("<i>Le plan est invalidé. C'est le scénario prévu, pas un "
                     "accident : une perte sur trois est normale.</i>")
    else:
        lines.append("")
        lines.append("<i>Le plan a atteint son objectif.</i>")

    lines.append("")
    lines.append(f"<i>{esc(DISCLAIMER.splitlines()[0])}</i>")
    return "\n".join(lines)


def send_events(events: list, *, dry_run: bool = False) -> dict[str, Any]:
    envoyes, erreurs = 0, []
    for event in events:
        resultat = send(format_event(event), dry_run=dry_run)
        if resultat["ok"]:
            envoyes += 1
        else:
            erreurs.append(resultat["error"])
    return {"ok": envoyes == len(events), "sent": envoyes,
            "total": len(events), "errors": erreurs}
