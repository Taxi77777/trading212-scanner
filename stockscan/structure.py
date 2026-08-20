from __future__ import annotations

"""Lecture structurelle d'une valeur : tendance, base, résistance, volume,
accumulation, compression, extension.

Tout part du prix, du volume et de leur géométrie — jamais d'un oscillateur
(§16). Chaque fonction renvoie un objet porteur de ses composantes, pour que le
score final soit explicable ligne à ligne (§38) plutôt qu'un nombre opaque.
"""

from dataclasses import dataclass, field
from statistics import fmean, median, pstdev

from . import market_data as md

__all__ = ["TrendState", "Base", "Resistance", "VolumeProfile", "Accumulation",
           "Compression", "Extension", "trend", "detect_base", "find_resistances",
           "volume_profile", "accumulation", "compression", "extension"]

HAUSSIERE, NEUTRE, BAISSIERE = "HAUSSIERE", "NEUTRE", "BAISSIERE"


# --------------------------------------------------------------------------- #
# §5 — tendance de fond
# --------------------------------------------------------------------------- #
@dataclass
class TrendState:
    direction: str = NEUTRE
    score: float = 0.0            # 0 à 10
    above_ma50: bool = False
    above_ma200: bool = False
    ma50_rising: bool = False
    ma200_rising: bool = False
    higher_highs: bool = False
    higher_lows: bool = False
    notes: list[str] = field(default_factory=list)


def trend(bars: md.Bars | None, *, fast: int = 50, slow: int = 200) -> TrendState:
    """Tendance à partir de la structure des sommets/creux et des moyennes."""
    state = TrendState()
    if not bars or len(bars) < 60:
        state.notes.append("historique insuffisant")
        return state

    close = bars.close
    price = close[-1]
    ma_fast_series = md.sma_series(close, min(fast, len(close)))
    ma_slow_series = md.sma_series(close, min(slow, len(close))) if len(close) >= slow else []

    points = 0.0
    if ma_fast_series:
        state.above_ma50 = price > ma_fast_series[-1]
        state.ma50_rising = len(ma_fast_series) > 10 and ma_fast_series[-1] > ma_fast_series[-11]
        points += 2.0 if state.above_ma50 else 0.0
        points += 1.5 if state.ma50_rising else 0.0
    if ma_slow_series:
        state.above_ma200 = price > ma_slow_series[-1]
        state.ma200_rising = len(ma_slow_series) > 20 and ma_slow_series[-1] > ma_slow_series[-21]
        points += 2.5 if state.above_ma200 else 0.0
        points += 1.0 if state.ma200_rising else 0.0
    else:
        # Historique court, pas de MM200. Un forfait fixe de 1,75 point rendait
        # HAUSSIERE inatteignable (le seuil est à 5,5) tout en laissant
        # BAISSIERE accessible : une valeur récemment cotée était classée
        # baissière si elle baissait, et jamais haussière si elle montait.
        # On reporte donc sur la MM50 le crédit qu'aurait porté la MM200.
        points += 2.5 if state.above_ma50 else 0.0
        points += 1.0 if state.ma50_rising else 0.0

    highs = md.swing_highs(bars, 4, 4)[-3:]
    lows = md.swing_lows(bars, 4, 4)[-3:]
    if len(highs) >= 2:
        state.higher_highs = highs[-1][1] > highs[-2][1]
        points += 1.5 if state.higher_highs else 0.0
    if len(lows) >= 2:
        state.higher_lows = lows[-1][1] > lows[-2][1]
        points += 1.5 if state.higher_lows else 0.0

    state.score = round(min(10.0, points), 2)
    # Symétrie : sans MM200, on ne peut ni confirmer ni infirmer le fond. La
    # version précédente rendait HAUSSIERE inatteignable en dessous de 200
    # séances tout en laissant BAISSIERE accessible — un biais baissier
    # silencieux sur toute valeur récemment cotée.
    long_term_ok = state.above_ma200 if ma_slow_series else state.ma50_rising
    long_term_broken = (not state.above_ma200) if ma_slow_series else (not state.above_ma50)
    healthy = long_term_ok and (state.above_ma50 or state.ma50_rising)
    broken = long_term_broken and (not state.ma50_rising)
    state.direction = HAUSSIERE if healthy and state.score >= 5.5 else \
                      BAISSIERE if broken and state.score <= 3.0 else NEUTRE

    if state.above_ma200:
        state.notes.append("au-dessus de la MM200")
    else:
        state.notes.append("sous la MM200")
    if state.higher_lows:
        state.notes.append("creux ascendants")
    if state.higher_highs:
        state.notes.append("sommets ascendants")
    return state


# --------------------------------------------------------------------------- #
# §6 — bases de consolidation
# --------------------------------------------------------------------------- #
FLAT_BASE, CUP_HANDLE, DOUBLE_BOTTOM, RANGE, TRIANGLE, NO_BASE = \
    "FLAT_BASE", "CUP_HANDLE", "DOUBLE_BOTTOM", "RANGE", "TRIANGLE", "AUCUNE"

BASE_LABELS = {
    FLAT_BASE: "Flat Base", CUP_HANDLE: "Cup & Handle", DOUBLE_BOTTOM: "Double Bottom",
    RANGE: "Range horizontal", TRIANGLE: "Triangle", NO_BASE: "Aucune base nette",
}


@dataclass
class Base:
    kind: str = NO_BASE
    label: str = BASE_LABELS[NO_BASE]
    length: int = 0
    high: float = 0.0
    low: float = 0.0
    depth_pct: float = 0.0        # profondeur de la correction dans la base
    width_pct: float = 0.0        # amplitude haut/bas
    resistance_tests: int = 0
    support_tests: int = 0
    tightness: float = 0.0        # écart-type des clôtures, en % — plus bas = plus propre
    quality: float = 0.0          # 0 à 15
    notes: list[str] = field(default_factory=list)

    @property
    def found(self) -> bool:
        return self.kind != NO_BASE


def _touches(values: list[float], level: float, tolerance: float) -> int:
    """Nombre d'approches distinctes d'un niveau (rebonds séparés)."""
    count, armed = 0, True
    for v in values:
        near = abs(v - level) <= tolerance
        if near and armed:
            count += 1
            armed = False
        elif not near and abs(v - level) > tolerance * 2:
            armed = True
    return count


def detect_base(bars: md.Bars | None, *, min_len: int = 15, max_len: int = 130) -> Base:
    """Cherche la meilleure base récente, la plus longue et la plus propre.

    Une base est une zone où le prix cesse de progresser et se resserre. On teste
    plusieurs longueurs et on garde celle dont la qualité est la meilleure, plutôt
    que d'imposer une fenêtre arbitraire.
    """
    base = Base()
    if not bars or len(bars) < min_len + 10:
        base.notes.append("historique insuffisant")
        return base

    best: Base | None = None
    for length in range(min_len, min(max_len, len(bars) - 5) + 1, 5):
        window = bars.tail(length)
        hi, lo = max(window.high), min(window.low)
        if lo <= 0 or hi <= lo:
            continue
        width = (hi - lo) / lo * 100
        if width > 45:
            continue                      # trop large pour être une base

        closes = window.close
        mean_close = fmean(closes)
        tight = (pstdev(closes) / mean_close * 100) if mean_close else 99.0
        tol = (hi - lo) * 0.06
        r_tests = _touches(window.high, hi, tol)
        s_tests = _touches(window.low, lo, tol)
        depth = (hi - lo) / hi * 100

        # Qualité : longue, resserrée, résistance testée, correction contenue.
        q = 0.0
        q += min(4.0, length / 30)                       # durée
        q += max(0.0, 4.0 - tight / 3.0)                 # propreté
        q += min(3.0, (r_tests - 1) * 1.2)               # résistance éprouvée
        q += 2.5 if depth <= 20 else 1.2 if depth <= 32 else 0.0
        q += 1.5 if width <= 18 else 0.7 if width <= 28 else 0.0

        # La base doit être une pause, pas une chute : le bas ne doit pas être récent.
        low_idx = window.low.index(lo)
        if low_idx > length * 0.8:
            q -= 2.0
            
        candidate = Base(kind=RANGE, label=BASE_LABELS[RANGE], length=length,
                         high=hi, low=lo, depth_pct=round(depth, 2),
                         width_pct=round(width, 2), resistance_tests=r_tests,
                         support_tests=s_tests, tightness=round(tight, 2),
                         quality=round(max(0.0, min(15.0, q)), 2))
        if best is None or candidate.quality > best.quality:
            best = candidate

    if best is None or best.quality < 4.0:
        base.notes.append("pas de consolidation exploitable")
        return base

    best.kind, best.label = _classify_base(bars.tail(best.length), best)
    if best.resistance_tests >= 2:
        best.notes.append(f"résistance testée {best.resistance_tests} fois")
    if best.tightness <= 4:
        best.notes.append("consolidation resserrée")
    if best.depth_pct <= 15:
        best.notes.append("correction contenue")
    return best


def _classify_base(window: md.Bars, base: Base) -> tuple[str, str]:
    """Nomme la figure à partir de la géométrie de la fenêtre."""
    n = len(window)
    if n < 12:
        return RANGE, BASE_LABELS[RANGE]
    lows = window.low
    third = n // 3
    left, middle, right = lows[:third], lows[third:2 * third], lows[2 * third:]
    lo_left, lo_mid, lo_right = min(left), min(middle), min(right)
    span = base.high - base.low or 1.0

    # Cup & Handle : creux au centre, bords hauts, petite anse en fin de figure.
    if lo_mid < lo_left - span * 0.15 and lo_mid < lo_right - span * 0.15:
        handle = window.tail(max(4, n // 6))
        if max(handle.high) < base.high * 0.995:
            return CUP_HANDLE, BASE_LABELS[CUP_HANDLE]
    # Double Bottom : deux creux comparables, tous deux réellement au plancher
    # de la base, séparés par un rebond franc. Sans la contrainte de plancher,
    # n'importe quelle oscillation régulière était étiquetée double bottom.
    at_floor = (lo_left <= base.low + span * 0.22) and (lo_right <= base.low + span * 0.22)
    # Deux creux — pas dix. Une oscillation régulière revient sur son plancher à
    # chaque cycle et satisfait toutes les conditions géométriques d'un double
    # bottom ; c'est le NOMBRE de visites qui les sépare.
    few_visits = base.support_tests <= 3
    if (at_floor and few_visits and abs(lo_left - lo_right) <= span * 0.12
            and max(middle) > min(lo_left, lo_right) + span * 0.35):
        return DOUBLE_BOTTOM, BASE_LABELS[DOUBLE_BOTTOM]
    # Triangle : amplitude qui se contracte franchement.
    first_half = window.head(n // 2)
    second_half = window.tail(n // 2)
    w1 = max(first_half.high) - min(first_half.low)
    w2 = max(second_half.high) - min(second_half.low)
    if w1 > 0 and w2 / w1 < 0.62:
        return TRIANGLE, BASE_LABELS[TRIANGLE]
    # Flat Base : très resserrée et peu profonde.
    if base.width_pct <= 15 and base.tightness <= 4.5:
        return FLAT_BASE, BASE_LABELS[FLAT_BASE]
    return RANGE, BASE_LABELS[RANGE]


# --------------------------------------------------------------------------- #
# §10 — résistances
# --------------------------------------------------------------------------- #
@dataclass
class Resistance:
    level: float
    tests: int
    bars_since_last_test: int
    source: str
    distance_pct: float = 0.0
    quality: float = 0.0          # 0 à 10


def find_resistances(bars: md.Bars | None, *, top: int = 4) -> list[Resistance]:
    """Niveaux au-dessus du prix, classés par pertinence."""
    if not bars or len(bars) < 40:
        return []
    price = bars.close[-1]
    if price <= 0:
        return []

    # Les deux dernières bougies sont exclues de la recherche de niveaux : le
    # plus haut d'aujourd'hui n'est pas une résistance, c'est le prix du jour.
    formed = bars.head(len(bars) - 2)
    candidates: dict[float, tuple[int, str]] = {}

    def add(level: float, source: str) -> None:
        if level is None or level <= price * 1.0005 or level > price * 1.60:
            return
        for known in list(candidates):
            if abs(known - level) / level < 0.012:      # même zone
                return
        candidates[level] = (0, source)

    for window, label in ((20, "plus haut 20 j"), (50, "plus haut 50 j"),
                          (120, "plus haut 6 mois"), (252, "plus haut 52 sem.")):
        if len(formed) >= window:
            add(max(formed.high[-window:]), label)
    for _, level in md.swing_highs(formed, 4, 4)[-12:]:
        add(level, "sommet local")

    out: list[Resistance] = []
    for level, (_, source) in candidates.items():
        tol = level * 0.015
        tests = _touches(bars.high[-252:] if len(bars) >= 252 else bars.high, level, tol)
        last = 0
        for i in range(len(bars) - 1, -1, -1):
            if abs(bars.high[i] - level) <= tol:
                last = len(bars) - 1 - i
                break
        distance = (level / price - 1) * 100
        # Une bonne résistance est proche, éprouvée, et pas testée hier.
        quality = 0.0
        quality += 4.0 if distance <= 3 else 3.0 if distance <= 6 else 1.5 if distance <= 12 else 0.5
        quality += min(3.5, tests * 1.2)
        quality += 1.5 if 5 <= last <= 120 else 0.5
        quality += 1.0 if "52" in source or "6 mois" in source else 0.0
        # Un niveau touché à l'instant et jamais éprouvé n'est pas une résistance.
        if last < 3 and tests < 2:
            continue
        out.append(Resistance(round(level, 4), tests, last, source,
                              round(distance, 2), round(min(10.0, quality), 2)))
    out.sort(key=lambda r: r.quality, reverse=True)
    return out[:top]


# --------------------------------------------------------------------------- #
# §9 — volume
# --------------------------------------------------------------------------- #
def nearest_above(levels: list[Resistance], price: float) -> Resistance | None:
    """La résistance la plus PROCHE au-dessus du cours, pas la mieux notée.

    `find_resistances` classe par qualité : un plus haut annuel testé une fois
    passait devant un sommet local situé 3 % au-dessus du cours. Pour juger
    d'une pré-cassure, ce qui compte est le premier obstacle rencontré, pas le
    plus prestigieux. La qualité continue de départager deux niveaux voisins.
    """
    above = [r for r in levels if r.level > price]
    if not above:
        return None
    floor = min(r.level for r in above)
    close_ones = [r for r in above if r.level <= floor * 1.01]
    return max(close_ones, key=lambda r: r.quality)


@dataclass
class VolumeProfile:
    last: float = 0.0
    avg20: float = 0.0
    avg50: float = 0.0
    ratio20: float = 0.0
    ratio50: float = 0.0
    up_down_ratio: float = 1.0     # volume des séances hausse / séances baisse
    dry_up: bool = False           # volume qui s'assèche dans la base
    expanding: bool = False        # volume qui reprend
    score: float = 0.0             # 0 à 15
    notes: list[str] = field(default_factory=list)


def volume_profile(bars: md.Bars | None, base: Base | None = None) -> VolumeProfile:
    vp = VolumeProfile()
    if not bars or len(bars) < 55:
        vp.notes.append("historique insuffisant")
        return vp

    vp.last = bars.volume[-1]
    vp.avg20 = fmean(bars.volume[-20:])
    vp.avg50 = fmean(bars.volume[-50:])
    vp.ratio20 = round(vp.last / vp.avg20, 2) if vp.avg20 else 0.0
    vp.ratio50 = round(vp.last / vp.avg50, 2) if vp.avg50 else 0.0

    up_vol = sum(v for c, p, v in zip(bars.close[-40:], bars.close[-41:-1], bars.volume[-40:]) if c > p)
    dn_vol = sum(v for c, p, v in zip(bars.close[-40:], bars.close[-41:-1], bars.volume[-40:]) if c < p)
    vp.up_down_ratio = round(up_vol / dn_vol, 2) if dn_vol else 2.0

    recent = fmean(bars.volume[-10:])
    vp.dry_up = bool(vp.avg50) and recent < vp.avg50 * 0.85
    vp.expanding = bool(vp.avg20) and fmean(bars.volume[-3:]) > vp.avg20 * 1.15

    score = 0.0
    # Qualité du volume : hausse sur les avancées, retrait sur les replis.
    score += 5.0 if vp.up_down_ratio >= 1.35 else 3.5 if vp.up_down_ratio >= 1.1 else \
             1.5 if vp.up_down_ratio >= 0.9 else 0.0
    # Assèchement dans la base : signe classique de fin de distribution.
    if base is not None and base.found and vp.dry_up:
        score += 3.5
        vp.notes.append("volume asséché dans la base")
    elif vp.dry_up:
        score += 1.5
    # Reprise du volume — utile avant cassure, décisive pendant.
    if vp.expanding:
        score += 3.5
        vp.notes.append(f"volume en reprise ({vp.ratio20:.2f}× la moyenne 20 j)")
    elif vp.ratio20 >= 1.2:
        score += 2.0
    # Un volume soutenu sur 50 séances traduit de l'intérêt réel.
    if vp.avg50 and vp.avg20 / vp.avg50 >= 1.05:
        score += 3.0
        vp.notes.append("intérêt croissant sur 20 j vs 50 j")
    elif vp.avg50 and vp.avg20 / vp.avg50 >= 0.95:
        score += 1.5

    vp.score = round(min(15.0, score), 2)
    return vp


# --------------------------------------------------------------------------- #
# §7 et §8 — accumulation et OBV
# --------------------------------------------------------------------------- #
@dataclass
class Accumulation:
    obv_change_volumes: float = 0.0   # variation d'OBV en journées de volume moyen
    obv_change_pct: float = 0.0       # alias historique, même valeur
    obv_near_high: bool = False
    obv_new_high: bool = False
    price_below_resistance: bool = False
    positive_divergence: bool = False
    negative_divergence: bool = False
    score: float = 0.0             # 0 à 10
    notes: list[str] = field(default_factory=list)


def accumulation(bars: md.Bars | None, resistance: Resistance | None = None,
                 *, lookback: int = 60) -> Accumulation:
    """Détecte l'accumulation : l'OBV progresse pendant que le prix patiente."""
    acc = Accumulation()
    if not bars or len(bars) < lookback + 10:
        acc.notes.append("historique insuffisant")
        return acc

    obv = md.obv_series(bars)
    if len(obv) < lookback + 1:
        return acc
    recent = obv[-lookback:]
    span = max(recent) - min(recent)

    # L'OBV est une somme cumulée : son niveau absolu dépend de la quantité
    # d'historique chargée, pas du titre. Un pourcentage calculé sur ce niveau
    # change du simple au quadruple selon qu'on a lu 120 ou 300 séances — le
    # chiffre ne mesure alors plus rien. On exprime la variation en JOURNÉES DE
    # VOLUME MOYEN : +8 signifie « huit séances moyennes d'achat net accumulées ».
    avg_volume = fmean(bars.volume[-lookback:]) if bars.volume else 0.0
    delta = obv[-1] - obv[-lookback - 1]
    acc.obv_change_volumes = round(delta / avg_volume, 2) if avg_volume else 0.0
    acc.obv_change_pct = acc.obv_change_volumes
    acc.obv_near_high = span > 0 and (max(recent) - obv[-1]) <= span * 0.12
    acc.obv_new_high = obv[-1] >= max(recent)

    price_change = md.pct_change(bars.close, lookback) or 0.0
    acc.price_below_resistance = bool(resistance and resistance.distance_pct > 0.4)

    score = 0.0
    if acc.obv_new_high:
        score += 4.0
        acc.notes.append("OBV sur un nouveau sommet")
    elif acc.obv_near_high:
        score += 2.5
        acc.notes.append("OBV proche de ses sommets")

    # Le scénario le plus recherché du §8 : prix encore sous résistance, OBV déjà au plus haut.
    if acc.price_below_resistance and acc.obv_new_high:
        score += 3.5
        acc.notes.append("prix sous résistance alors que l'OBV fait un sommet")

    # Divergences prix / OBV.
    obv_change = (obv[-1] - obv[-lookback - 1])
    if price_change <= 2.0 and obv_change > 0 and span > 0:
        acc.positive_divergence = True
        score += 2.5
        acc.notes.append("divergence positive prix / OBV")
    if price_change > 6.0 and obv_change < 0:
        acc.negative_divergence = True
        score -= 3.0
        acc.notes.append("divergence négative : distribution")

    lows = md.swing_lows(bars, 4, 4)[-3:]
    if len(lows) >= 2 and lows[-1][1] > lows[-2][1]:
        score += 1.5
        acc.notes.append("creux ascendants défendus")

    acc.score = round(max(0.0, min(10.0, score)), 2)
    return acc


# --------------------------------------------------------------------------- #
# §14 — compression de volatilité
# --------------------------------------------------------------------------- #
@dataclass
class Compression:
    atr_now: float = 0.0
    atr_ref: float = 0.0
    ratio: float = 1.0
    range_contraction: float = 1.0
    detected: bool = False
    score: float = 0.0             # 0 à 5
    notes: list[str] = field(default_factory=list)


def compression(bars: md.Bars | None, *, period: int = 14) -> Compression:
    """Volatilité qui se contracte : ATR récent contre ATR de référence."""
    comp = Compression()
    if not bars or len(bars) < 90:
        return comp
    # ATR en POURCENTAGE du prix : l'ATR brut croît avec le cours et rendrait
    # toute valeur ayant doublé « plus volatile » qu'avant, à comportement égal.
    series = md.atr_pct_series(bars, period)
    if len(series) < 70:
        return comp

    # La référence doit venir d'AVANT la contraction. Une fenêtre fixe courte
    # tombait à l'intérieur de la base elle-même et donnait toujours un ratio
    # proche de 1. On prend la médiane d'un historique long, insensible aux pics.
    comp.atr_now = round(fmean(series[-10:]), 4)
    reference = series[-260:-15] if len(series) >= 120 else series[:-15]
    comp.atr_ref = round(median(reference), 4) if reference else 0.0
    comp.ratio = round(comp.atr_now / comp.atr_ref, 3) if comp.atr_ref else 1.0

    # Amplitude également rapportée au prix, et surtout mesurée sur DEUX
    # FENÊTRES DE MÊME LONGUEUR. Comparer 15 séances à 45 donnait un rapport
    # d'environ 0,58 pour une volatilité rigoureusement constante — la racine
    # de 15/45 — c'est-à-dire un faux signal de contraction pour presque tout
    # le marché. La contraction se mesure à durée égale ou pas du tout.
    def rel_range(highs, lows, closes):
        ref = fmean(closes)
        return (max(highs) - min(lows)) / ref if ref else 0.0

    win = 15
    recent_range = rel_range(bars.high[-win:], bars.low[-win:], bars.close[-win:])
    priors = [rel_range(bars.high[-(win * k + win):-(win * k)],
                        bars.low[-(win * k + win):-(win * k)],
                        bars.close[-(win * k + win):-(win * k)])
              for k in range(1, 5) if len(bars) >= win * (k + 1)]
    priors = [r for r in priors if r > 0]
    older_range = median(priors) if priors else 0.0
    comp.range_contraction = round(recent_range / older_range, 3) if older_range else 1.0

    score = 0.0
    if comp.ratio <= 0.70:
        score += 3.0
        comp.notes.append(f"ATR à {comp.ratio:.2f}× sa référence")
    elif comp.ratio <= 0.85:
        score += 2.0
    elif comp.ratio <= 0.95:
        score += 1.0
    if comp.range_contraction <= 0.55:
        score += 2.0
        comp.notes.append("amplitude fortement contractée")
    elif comp.range_contraction <= 0.75:
        score += 1.0

    comp.score = round(min(5.0, score), 2)
    comp.detected = comp.score >= 2.5
    return comp


# --------------------------------------------------------------------------- #
# §21 — extension : pénaliser ce qui a déjà couru
# --------------------------------------------------------------------------- #
@dataclass
class Extension:
    change_5d: float = 0.0
    change_20d: float = 0.0
    above_ma50_pct: float = 0.0
    penalty: float = 0.0           # points retirés au score global
    disqualifies_prebreakout: bool = False
    notes: list[str] = field(default_factory=list)


def extension(bars: md.Bars | None) -> Extension:
    ext = Extension()
    if not bars or len(bars) < 55:
        return ext
    ext.change_5d = round(md.pct_change(bars.close, 5) or 0.0, 2)
    ext.change_20d = round(md.pct_change(bars.close, 20) or 0.0, 2)
    ma50 = md.sma(bars.close, 50)
    if ma50:
        ext.above_ma50_pct = round((bars.close[-1] / ma50 - 1) * 100, 2)

    move = max(ext.change_5d, ext.change_20d)
    if move >= 30:
        ext.penalty = 25.0
        ext.disqualifies_prebreakout = True
        ext.notes.append(f"déjà +{move:.0f} % — le mouvement a eu lieu")
    elif move >= 20:
        ext.penalty = 14.0
        ext.notes.append(f"déjà +{move:.0f} % — forte extension")
    elif move >= 15:
        ext.penalty = 8.0
        ext.notes.append(f"déjà +{move:.0f} %")
    elif move >= 10:
        ext.penalty = 3.0
    # Trop loin de la MM50 : le retour à la moyenne devient le risque dominant.
    if ext.above_ma50_pct >= 25:
        ext.penalty += 6.0
        ext.notes.append(f"{ext.above_ma50_pct:.0f} % au-dessus de la MM50")
    return ext
