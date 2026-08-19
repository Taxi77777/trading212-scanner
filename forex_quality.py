from __future__ import annotations

"""Multi-factor coherence gate and medal ranking for Forex signals.

Two responsibilities, deliberately kept separate from signal generation:

``coherence(sig)``
    Evaluates whether the proposed direction is defensible against *every*
    available factor (D1/H4/H1/M15, DXY, relative strength, macro, rates,
    volatility, liquidity, correlation, news, R:R). It never blocks on a single
    factor — in particular a USD/CHF BUY with a bearish DXY is perfectly
    coherent when the rest of the picture supports it. A signal is rejected
    only when several *independent* factors contradict it.

``medal(sig)``
    Ranks a validated signal 🥇/🥈/🥉 on overall quality, not on the raw
    quantitative score alone.

Neither function can create a signal, raise a score above what the engine
produced, or lower a threshold.
"""

from typing import Any

__all__ = ["coherence", "quality", "medal", "COHERENT", "MITIGE", "INCOHERENT"]

COHERENT = "COHERENT"
MITIGE = "MITIGE"
INCOHERENT = "INCOHERENT"

# A signal is dropped once contradicting evidence reaches this weight *and*
# comes from at least this many independent factors. Both conditions are
# required so that one noisy input can never veto the engine on its own.
CONTRA_WEIGHT_BLOCK = float(6.0)
CONTRA_FACTORS_BLOCK = int(3)


def _bull(value: object) -> int:
    text = str(value or "").upper()
    if "BULL" in text:
        return 1
    if "BEAR" in text:
        return -1
    return 0


def _side_sign(side: object) -> int:
    return 1 if str(side or "").upper() == "BUY" else -1


def _parse_delta(text: object) -> float | None:
    """Parse ``"EUR +1.4 vs USD"`` / ``"CAD +0.31% vs CHF (4-24h)"``."""
    for token in str(text or "").replace("vs", " ").split():
        if token[:1] in "+-":
            try:
                return float(token.rstrip("%,)"))
            except ValueError:
                continue
    return None


def _strength_delta(sig: Any) -> float:
    return _parse_delta(getattr(sig, "strength", "")) or 0.0


def coherence(sig: Any) -> dict[str, Any]:
    """Return ``{verdict, score, pro, contra, notes}`` for *sig*."""
    sign = _side_sign(getattr(sig, "side", ""))
    pro: list[str] = []
    contra: list[str] = []
    pro_weight = 0.0
    contra_weight = 0.0

    def add(ok: bool | None, weight: float, ok_note: str, ko_note: str) -> None:
        nonlocal pro_weight, contra_weight
        if ok is None:
            return
        if ok:
            pro_weight += weight
            pro.append(ok_note)
        else:
            contra_weight += weight
            contra.append(ko_note)

    # --- Multi-timeframe structure ----------------------------------------
    # D1, H4 and H1 are the same price series resampled, so they are strongly
    # collinear: scoring them as three independent confirmations inflates the
    # apparent confluence of what is really a single fact. They are collapsed
    # into one factor whose weight reflects how unanimous they are.
    aligned, opposed = [], []
    for label, attr in (("D1", "d1"), ("H4", "h4"), ("H1", "h1")):
        bias = _bull(getattr(sig, attr, ""))
        if bias == 0:
            continue
        (aligned if bias == sign else opposed).append(label)
    if aligned or opposed:
        total = len(aligned) + len(opposed)
        weight = 4.0 * (max(len(aligned), len(opposed)) / total)
        add(len(aligned) > len(opposed), weight,
            "structure " + "+".join(aligned) + " alignée",
            "structure " + "+".join(opposed) + " opposée")

    m15 = str(getattr(sig, "m15", "") or "").upper()
    if m15:
        add("CONFIRME" in m15, 1.5, "M15 confirmé", "M15 non confirmé")

    # --- Relative strength -------------------------------------------------
    delta = _strength_delta(sig)
    if abs(delta) >= 0.5:
        add(delta * sign > 0, 2.0, "force relative alignée", "force relative opposée")

    # --- Short-horizon strength ------------------------------------------- #
    # An M15 entry lives for minutes; the daily ranking describes the last few
    # weeks. When the two disagree, the trade is fighting the flow that is
    # actually running — weighted above the daily reading for that reason.
    intra = _parse_delta(getattr(sig, "strength_intraday", ""))
    if intra is not None and abs(intra) >= 0.05:
        add(intra * sign > 0, 2.5,
            "force intraday alignée", "force intraday opposée")
        if abs(delta) >= 0.5 and delta * intra < 0:
            contra_weight += 1.5
            contra.append("divergence force journalière / intraday")

    # --- DXY: contextual, never a standalone veto --------------------------
    symbol = str(getattr(sig, "symbol", "") or "")
    pair = str(getattr(sig, "pair", "") or "")
    dxy = str(getattr(sig, "dxy", "") or "").upper()
    usd_in_pair = "USD" in symbol.upper() or "USD" in pair.upper()
    if usd_in_pair and dxy in ("BULL", "BEAR"):
        usd_is_base = pair.upper().startswith("USD") or symbol.upper().startswith("USD")
        # Expected DXY direction for this trade to be "with the dollar".
        expected_usd_long = (sign > 0) if usd_is_base else (sign < 0)
        dxy_usd_long = dxy == "BULL"
        add(expected_usd_long == dxy_usd_long, 2.0, f"DXY {dxy} cohérent", f"DXY {dxy} opposé")

    # --- Correlation proxy -------------------------------------------------
    corr = str(getattr(sig, "correlation", "") or "").upper()
    if corr.startswith("CONFIRM"):
        add(True, 1.5, "corrélation confirme", "")
    elif corr.startswith("CONTRE"):
        add(False, 1.5, "", "corrélation opposée")

    # --- Macro regime ------------------------------------------------------
    macro = str(getattr(sig, "macro", "") or "").upper()
    base = pair.split("/")[0].upper() if "/" in pair else ""
    quote = pair.split("/")[-1].upper() if "/" in pair else ""
    risk_on_ccy = {"AUD", "NZD", "CAD"}
    haven_ccy = {"JPY", "CHF", "USD"}
    if macro in ("RISK-ON", "RISK-OFF") and base and quote:
        if macro == "RISK-ON":
            favours_buy = base in risk_on_ccy and quote in haven_ccy
            favours_sell = quote in risk_on_ccy and base in haven_ccy
        else:
            favours_buy = base in haven_ccy and quote in risk_on_ccy
            favours_sell = quote in haven_ccy and base in risk_on_ccy
        if favours_buy or favours_sell:
            add((favours_buy and sign > 0) or (favours_sell and sign < 0),
                1.0, f"macro {macro} cohérente", f"macro {macro} opposée")

    # --- Rate differential (added by the v4 overlay) -----------------------
    reasons = " ".join(str(r) for r in (getattr(sig, "reasons", []) or []))
    if "TAUX_FORTEMENT_CONTRE" in reasons:
        add(False, 2.0, "", "différentiel de taux fortement contraire")
    elif "TAUX_CONTRE" in reasons:
        add(False, 1.0, "", "différentiel de taux contraire")
    elif "TAUX_FORTEMENT_FAVORABLE" in reasons or "TAUX_FAVORABLE" in reasons:
        add(True, 1.0, "différentiel de taux favorable", "")

    # --- Regime quality (not directional: penalise unusable conditions) ----
    vol = str(getattr(sig, "vol_regime", "") or "").upper()
    if vol == "EXPLOSIVE":
        contra_weight += 1.0
        contra.append("volatilité explosive")
    elif vol == "FAIBLE":
        contra_weight += 0.5
        contra.append("volatilité faible")
    elif vol in ("NORMALE", "ELEVEE"):
        pro_weight += 0.5
        pro.append(f"volatilité {vol.lower()}")

    liq = str(getattr(sig, "liquidity", "") or "").upper()
    if liq.startswith("SWEEP"):
        pro_weight += 1.5
        pro.append("prise de liquidité")
    elif liq in ("ABOVE_PREVIOUS_DAY_HIGH", "BELOW_PREVIOUS_DAY_LOW"):
        pro_weight += 0.75
        pro.append("hors range de la veille")

    try:
        rr = float(getattr(sig, "rr", 0) or 0)
    except (TypeError, ValueError):
        rr = 0.0
    if rr >= 1.5:
        pro_weight += 1.0
        pro.append(f"R:R {rr:.1f}")
    elif 0 < rr < 1.2:
        contra_weight += 1.5
        contra.append(f"R:R insuffisant {rr:.1f}")

    news = str(getattr(sig, "news", "") or "").upper()
    if "HIGH IMPACT" in news and "AUCUN" not in news:
        contra_weight += 2.0
        contra.append("news high impact imminente")

    total = pro_weight + contra_weight
    score = int(round(100 * pro_weight / total)) if total else 50

    if contra_weight >= CONTRA_WEIGHT_BLOCK and len(contra) >= CONTRA_FACTORS_BLOCK:
        verdict = INCOHERENT
    elif contra_weight > pro_weight:
        verdict = INCOHERENT
    elif contra:
        verdict = MITIGE
    else:
        verdict = COHERENT

    return {
        "verdict": verdict,
        "score": score,
        "pro": pro,
        "contra": contra,
        "pro_weight": round(pro_weight, 2),
        "contra_weight": round(contra_weight, 2),
    }


# --------------------------------------------------------------------------- #
# Medals
# --------------------------------------------------------------------------- #
_AI_POINTS = {
    "CONFIRME": 100.0,
    "PRUDENCE": 45.0,
    "INDISPONIBLE": 50.0,   # neutral: an offline AI must not penalise the engine
    "CONTRADICTION": 0.0,
}


def quality(sig: Any, coherence_result: dict[str, Any] | None = None) -> dict[str, Any]:
    """Overall 0-100 quality, blending the engine, the context and the AI."""
    coh = coherence_result or coherence(sig)

    try:
        quant = max(0.0, min(100.0, float(getattr(sig, "score", 0) or 0)))
    except (TypeError, ValueError):
        quant = 0.0

    verdict = str(getattr(sig, "ai_verdict", "INDISPONIBLE") or "INDISPONIBLE").upper()
    ai_base = _AI_POINTS.get(verdict, 50.0)
    if verdict == "CONFIRME":
        try:
            confidence = max(0.0, min(100.0, float(getattr(sig, "ai_confidence", 0) or 0)))
        except (TypeError, ValueError):
            confidence = 0.0
        # A confirmation with no stated confidence still beats a plain PRUDENCE.
        ai_base = 70.0 + 0.30 * confidence

    try:
        rr = float(getattr(sig, "rr", 0) or 0)
    except (TypeError, ValueError):
        rr = 0.0
    rr_points = max(0.0, min(100.0, (rr - 1.0) / 1.5 * 100.0))

    m15_points = 100.0 if "CONFIRME" in str(getattr(sig, "m15", "")).upper() else 35.0

    components = (
        (quant, 0.40),
        (float(coh["score"]), 0.30),
        (ai_base, 0.15),
        (rr_points, 0.08),
        (m15_points, 0.07),
    )
    total = sum(value * weight for value, weight in components)
    return {
        "quality": int(round(max(0.0, min(100.0, total)))),
        "coherence": coh,
        "components": {
            "quant": round(quant, 1),
            "coherence": coh["score"],
            "ai": round(ai_base, 1),
            "rr": round(rr_points, 1),
            "m15": m15_points,
        },
    }


def medal(quality_score: int) -> tuple[str, str]:
    """Return ``(emoji, name)``; empty strings when the signal earns no medal."""
    if quality_score >= 78:
        return "🥇", "OR"
    if quality_score >= 66:
        return "🥈", "ARGENT"
    if quality_score >= 54:
        return "🥉", "BRONZE"
    return "", ""
