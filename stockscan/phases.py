"""Phases du cycle et plan de risque.

Accumulation -> Compression -> Pré-cassure -> Cassure -> Retest -> Accélération.

La phase n'est pas déduite de la base détectée : après une cassure, la fenêtre
de la base contient déjà la cassure et son sommet est faussé. On repart donc du
pivot, c'est-à-dire du plus haut atteint AVANT le mouvement en cours.

Ce module ne passe aucun ordre. Il décrit un plan — point d'entrée, point
d'invalidation, objectifs — que l'utilisateur exécute lui-même s'il le décide.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import market_data as md
from . import structure as st
from . import strength as sg

EARLY = "EARLY"
PRE_BREAKOUT = "PRE_BREAKOUT"
BREAKOUT = "BREAKOUT"
RETEST = "RETEST"
ACCELERATION = "ACCELERATION"
NO_TRADE = "NO_TRADE"

PHASE_LABELS = {
    EARLY: "Accumulation précoce",
    PRE_BREAKOUT: "Pré-cassure",
    BREAKOUT: "Cassure",
    RETEST: "Retest",
    ACCELERATION: "Accélération (déjà partie)",
    NO_TRADE: "Aucun signal",
}

PHASE_EMOJI = {
    EARLY: "🌱",
    PRE_BREAKOUT: "🎯",
    BREAKOUT: "🚀",
    RETEST: "🔄",
    ACCELERATION: "⏭️",
    NO_TRADE: "⛔",
}


@dataclass
class Phase:
    name: str = NO_TRADE
    label: str = PHASE_LABELS[NO_TRADE]
    pivot: float = 0.0             # niveau de référence de la phase
    bars_since_breakout: int | None = None
    distance_to_pivot_pct: float = 0.0
    volume_confirms: bool = False
    reasons: list[str] = field(default_factory=list)

    @property
    def emoji(self) -> str:
        return PHASE_EMOJI.get(self.name, "")


BREAK_WINDOW = 25          # on regarde au plus loin le dernier mois de bourse
BREAK_MARGIN = 1.005       # 0,5 % — en dessous, c'est du bruit, pas une cassure


def _pivot_before(bars: md.Bars, offset: int, lookback: int = 60) -> float | None:
    """Le niveau qui barrait la route AVANT le mouvement en cours.

    On tronque la serie `offset` seances plus tot et on cherche la resistance la
    plus PROCHE au-dessus du cours d'alors. Prendre le plus haut absolu ferait
    disparaitre toute cassure de base tant que l'ancien sommet du rallye n'est
    pas depasse — alors que c'est bien la base qui bloquait le cours.
    """
    cut = len(bars) - offset
    if cut < 40:
        return None
    prior = bars.head(cut)
    price = prior.close[-1]
    nearest = st.nearest_above(st.find_resistances(prior, top=4), price)
    if nearest is not None:
        return nearest.level
    seg = prior.high[-lookback:]
    return max(seg) if seg else None


def _recent_breakout(bars: md.Bars, *, window: int = BREAK_WINDOW):
    """Premiere seance ayant cloture franchement au-dessus du pivot.

    On garde la PREMIERE, pas la derniere : c'est elle qui date le mouvement.
    Un retour durable sous le pivot annule la cassure.
    """
    pivot = _pivot_before(bars, window)
    if not pivot or pivot <= 0:
        return None
    n = len(bars)
    idx = None
    for i in range(max(0, n - window), n):
        if bars.close[i] > pivot * BREAK_MARGIN:
            if idx is None:
                idx = i
        elif idx is not None and bars.close[i] < pivot * 0.97:
            idx = None
    return None if idx is None else (idx, pivot)


def classify(bars: md.Bars | None, *, base: st.Base, resistance: st.Resistance | None,
             comp: st.Compression, volume: st.VolumeProfile,
             accum: st.Accumulation, ext: st.Extension,
             trend: st.TrendState) -> Phase:
    ph = Phase()
    if not bars or len(bars) < 60:
        ph.reasons.append("Historique insuffisant")
        return ph

    price = bars.close[-1]
    n = len(bars)
    bo = _recent_breakout(bars)

    if bo is not None:
        idx, pivot = bo
        since = n - 1 - idx
        ph.pivot = pivot
        ph.bars_since_breakout = since
        ph.distance_to_pivot_pct = (price / pivot - 1.0) * 100.0
        vol_at_break = bars.volume[idx]
        avg = md.sma(bars.volume[:idx], 50) or 0.0
        ph.volume_confirms = bool(avg and vol_at_break >= avg * 1.4)

        if ph.distance_to_pivot_pct > 15 or ext.disqualifies_prebreakout:
            ph.name = ACCELERATION
            ph.reasons.append(
                f"Cours à {ph.distance_to_pivot_pct:.1f} % au-dessus du pivot {pivot:.2f} "
                "— le mouvement est engagé, le risque d'entrée est mauvais")
        elif since <= 2:
            ph.name = BREAKOUT
            ph.reasons.append(f"Cassure du pivot {pivot:.2f} il y a {since} séance(s)")
            ph.reasons.append("Volume de cassure au rendez-vous" if ph.volume_confirms
                              else "Cassure sans expansion de volume — confirmation faible")
        elif -2.0 <= ph.distance_to_pivot_pct <= 4.0:
            ph.name = RETEST
            ph.reasons.append(
                f"Retour sur l'ancienne résistance {pivot:.2f} "
                f"({ph.distance_to_pivot_pct:+.1f} %) après cassure il y a {since} séances")
        else:
            ph.name = BREAKOUT
            ph.reasons.append(
                f"Cassure du pivot {pivot:.2f} il y a {since} séances, "
                f"cours {ph.distance_to_pivot_pct:+.1f} % au-dessus")
        ph.label = PHASE_LABELS[ph.name]
        return ph

    if resistance is not None and resistance.level > 0:
        ph.pivot = resistance.level
        ph.distance_to_pivot_pct = (price / resistance.level - 1.0) * 100.0
    dist_below = -ph.distance_to_pivot_pct if ph.pivot else 999.0

    if ext.disqualifies_prebreakout:
        ph.name = ACCELERATION
        ph.reasons += ext.notes or ["Le titre a déjà fortement progressé"]
    elif (base.found and ph.pivot and dist_below <= 7.0 and comp.detected
          and trend.direction != st.BAISSIERE):
        ph.name = PRE_BREAKOUT
        ph.reasons.append(f"{base.label} sous {ph.pivot:.2f}, à {dist_below:.1f} %")
        ph.reasons.append(f"Volatilité comprimée ({comp.ratio:.2f}× la référence)")
        if volume.dry_up:
            ph.reasons.append("Volume asséché — pas de pression vendeuse")
    elif (base.found and ph.pivot and dist_below <= 4.0
          and trend.direction != st.BAISSIERE):
        ph.name = PRE_BREAKOUT
        ph.reasons.append(f"Cours collé sous {ph.pivot:.2f} ({dist_below:.1f} %) "
                          "dans une consolidation")
        ph.reasons.append("Compression non confirmée — surveiller")
    elif base.found and accum.score >= 5 and trend.direction != st.BAISSIERE:
        ph.name = EARLY
        ph.reasons.append("Accumulation en cours, mais la résistance est encore loin "
                          f"({dist_below:.1f} %)")
    else:
        ph.name = NO_TRADE
        if not base.found:
            ph.reasons.append("Pas de base exploitable")
        if trend.direction == st.BAISSIERE:
            ph.reasons.append("Tendance de fond baissière")
        if not ph.reasons:
            ph.reasons.append("Aucune configuration en place")

    ph.label = PHASE_LABELS[ph.name]
    return ph


# --------------------------------------------------------------------------
# Plan de risque
# --------------------------------------------------------------------------
@dataclass
class Target:
    label: str
    price: float
    r_multiple: float


@dataclass
class RiskPlan:
    tradeable: bool = False
    entry: float = 0.0
    entry_kind: str = ""
    stop: float = 0.0
    risk_pct: float = 0.0          # distance entrée -> stop, en %
    rr: float = 0.0                # sur l'objectif principal
    atr_pct: float = 0.0
    targets: list[Target] = field(default_factory=list)
    size_capped: bool = False
    position_pct: float = 0.0      # % du capital à engager
    notes: list[str] = field(default_factory=list)


MAX_RISK_PCT = 9.0                 # au-delà, le stop n'est plus un stop
MAX_POSITION_PCT = 20.0            # aucune ligne ne dépasse 20 % du capital


def risk_plan(bars: md.Bars | None, *, phase: Phase, base: st.Base,
              resistance: st.Resistance | None,
              regime: sg.MarketRegime | None = None,
              account_risk_pct: float = 1.0, min_rr: float = 2.0) -> RiskPlan:
    """Entrée, invalidation, objectifs — calculés, jamais recopiés d'un modèle.

    Le R:R n'est pas une constante : il tombe de la structure. Si la structure ne
    permet pas au moins `min_rr`, le plan renvoie tradeable=False. Un mauvais
    rapport n'est pas rattrapable par un bon dossier.
    """
    plan = RiskPlan()
    if not bars or len(bars) < 30:
        plan.notes.append("Historique insuffisant pour un plan")
        return plan

    price = bars.close[-1]
    atr = md.atr(bars, 14) or 0.0
    plan.atr_pct = round(atr / price * 100.0, 2) if price else 0.0

    if phase.name in (NO_TRADE, ACCELERATION):
        plan.notes.append("Aucun plan : " + (phase.reasons[0] if phase.reasons else phase.label))
        return plan

    pivot = phase.pivot or (resistance.level if resistance else 0.0)
    if pivot <= 0:
        plan.notes.append("Pas de niveau de référence — plan impossible")
        return plan

    if phase.name == PRE_BREAKOUT:
        plan.entry = round(pivot * 1.002, 4)
        plan.entry_kind = "Achat stop juste au-dessus du pivot"
    elif phase.name == BREAKOUT:
        plan.entry = round(price, 4)
        plan.entry_kind = "Au marché, cassure en cours"
    elif phase.name == RETEST:
        plan.entry = round(price, 4)
        plan.entry_kind = "Sur le retest de l'ancienne résistance"
    else:  # EARLY
        plan.entry = round(price, 4)
        plan.entry_kind = "Entrée précoce dans la base — position réduite"

    # L'invalidation se place sous la zone RÉCENTE de consolidation, pas sous le
    # plus bas de toute la base : sur une base longue et profonde, ce plus bas
    # date de plusieurs mois et n'invalide plus rien aujourd'hui.
    recent_low = min(bars.low[-20:])
    if base.found and base.low > 0:
        recent_low = max(recent_low, base.low)
    if phase.name in (BREAKOUT, RETEST) and pivot > 0:
        recent_low = max(recent_low, pivot * 0.97)
    structural = recent_low * 0.995

    # Un stop plus serré que le bruit quotidien saute sans que rien ne soit
    # invalidé. L'ancienne règle prenait le PLUS SERRÉ entre la structure et
    # 2 ATR : dans une base qui se resserre, l'ATR s'effondre et le stop
    # finissait à 0,9 % sous l'entrée d'une base profonde de 12 %, ce qui
    # gonflait mécaniquement le R:R affiché. On ne descend jamais sous 1,5 ATR.
    noise_floor = plan.entry - 1.5 * atr if atr else structural
    plan.stop = round(min(structural, noise_floor), 4)
    if plan.stop >= plan.entry:
        plan.notes.append("Stop incohérent avec l'entrée — plan rejeté")
        return plan

    risk = plan.entry - plan.stop
    plan.risk_pct = round(risk / plan.entry * 100.0, 2)
    if plan.risk_pct > MAX_RISK_PCT:
        plan.notes.append(
            f"Invalidation à {plan.risk_pct:.1f} % — trop loin, la structure ne protège pas")
        return plan

    # Les objectifs viennent de la FIGURE, pas d'une multiplication du risque.
    # Poser T1 = entrée + 2R rendait le R:R égal à 2,00 pour tout le monde et
    # transformait le filtre `min_rr` en décor : il ne pouvait qu'accepter (à
    # 2,0) ou tout refuser (au-dessus). Ici l'objectif est la hauteur de la base
    # reportée depuis le pivot — une mesure prise sur le graphique — et le R:R
    # qui en découle varie réellement d'un dossier à l'autre.
    if not (base.found and base.high > base.low):
        plan.notes.append("Aucun objectif mesurable sur le graphique — R:R non calculable")
        return plan
    move = base.high - base.low
    for label, extension_ in (("T1", 1.0), ("T2", 1.5), ("T3", 2.0)):
        price_level = pivot + move * extension_
        plan.targets.append(Target(label, round(price_level, 4),
                                   round((price_level - plan.entry) / risk, 2)))

    plan.rr = plan.targets[0].r_multiple
    if plan.rr < min_rr:
        plan.notes.append(
            f"R:R structurel de {plan.rr:.2f} sous le minimum de {min_rr:.1f} — "
            "l'objectif de la figure ne paie pas le risque")
        return plan

    mult = regime.size_multiplier if regime else 1.0
    if regime and not regime.allow_new_positions:
        plan.notes.append("Régime de marché défavorable — pas de nouvelle position")
        return plan
    if phase.name == EARLY:
        mult *= 0.5
    raw_size = account_risk_pct / plan.risk_pct * 100.0 * mult
    plan.size_capped = raw_size > MAX_POSITION_PCT
    plan.position_pct = round(min(raw_size, MAX_POSITION_PCT), 2)
    plan.tradeable = True
    plan.notes.append(
        f"Risque {account_risk_pct:.1f} % du capital -> environ {plan.position_pct:.1f} % "
        "du capital engagé, hors frais")
    if plan.size_capped:
        plan.notes.append(
            f"Taille plafonnée à {MAX_POSITION_PCT:.0f} % : un stop très serré "
            "n'autorise pas une ligne démesurée")
    plan.notes.append("Analyse seulement : aucun ordre n'est transmis à un courtier")
    return plan
