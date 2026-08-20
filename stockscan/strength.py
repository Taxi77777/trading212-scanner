"""Force relative et régime de marché.

Deux questions distinctes, souvent confondues :

  1. « Cette action fait-elle mieux que son marché ? »  -> force relative.
  2. « Ce marché est-il porteur en ce moment ? »        -> régime.

La première décide du classement entre candidats. La seconde décide s'il faut
prendre le moindre risque aujourd'hui. Une action forte dans un marché cassé
reste une action dans un marché cassé : le régime réduit la taille, il ne
transforme pas un NON en OUI.

§12 (force relative) et §28 (score de régime) du cahier des charges.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import sqrt
from statistics import fmean

from . import market_data as md

RISK_ON, NEUTRE, RISK_OFF = "RISK_ON", "NEUTRE", "RISK_OFF"

DAY = 86400


# --------------------------------------------------------------------------
# Force relative
# --------------------------------------------------------------------------
@dataclass
class RelativeStrength:
    rs_1m: float = 0.0             # écart de performance en points de %
    rs_3m: float = 0.0
    rs_6m: float = 0.0
    stock_3m: float = 0.0
    bench_3m: float = 0.0
    line_from_high_pct: float = 0.0   # distance de la ligne RS à son propre plus haut
    line_new_high: bool = False
    line_rising: bool = False
    outperforms: bool = False
    score: float = 0.0             # 0 à 15
    notes: list[str] = field(default_factory=list)
    available: bool = False


def align(stock: md.Bars | None, bench: md.Bars | None) -> tuple[list[float], list[float]]:
    """Apparie les clôtures sur la même journée civile.

    Les fuseaux et les jours fériés diffèrent d'une place à l'autre : comparer
    deux séries par index de tableau produit un décalage silencieux qui grandit
    au fil des mois. On apparie par date, pas par position.
    """
    if not stock or not bench or len(stock) == 0 or len(bench) == 0:
        return [], []
    bench_by_day = {t // DAY: c for t, c in zip(bench.ts, bench.close)}
    xs: list[float] = []
    ys: list[float] = []
    for t, c in zip(stock.ts, stock.close):
        b = bench_by_day.get(t // DAY)
        if b is None or b <= 0 or c <= 0:
            continue
        xs.append(c)
        ys.append(b)
    return xs, ys


def _change(values: list[float], lookback: int) -> float | None:
    if len(values) <= lookback:
        return None
    old = values[-1 - lookback]
    if old <= 0:
        return None
    return (values[-1] / old - 1.0) * 100.0


def relative_strength(stock: md.Bars | None, bench: md.Bars | None,
                      *, lookback: int = 126) -> RelativeStrength:
    rs = RelativeStrength()
    px, bx = align(stock, bench)
    if len(px) < 30:
        rs.notes.append("Historique commun insuffisant — force relative non calculée")
        return rs

    rs.available = True
    line = [p / b for p, b in zip(px, bx)]

    pairs = (("rs_1m", 21), ("rs_3m", 63), ("rs_6m", 126))
    for name, look in pairs:
        s = _change(px, look)
        b = _change(bx, look)
        if s is None or b is None:
            continue
        setattr(rs, name, s - b)
        if name == "rs_3m":
            rs.stock_3m, rs.bench_3m = s, b

    window = line[-min(lookback, len(line)):]
    peak = max(window)
    if peak > 0:
        rs.line_from_high_pct = (line[-1] / peak - 1.0) * 100.0
    rs.line_new_high = line[-1] >= peak * 0.999
    if len(line) > 21:
        rs.line_rising = line[-1] > line[-22]

    rs.outperforms = rs.rs_3m > 0

    score = 0.0
    if rs.line_new_high:
        score += 6.0
        rs.notes.append("Ligne de force relative sur un plus haut — l'action mène")
    elif rs.line_from_high_pct > -3.0:
        score += 4.0
        rs.notes.append("Force relative à moins de 3 % de son sommet")
    elif rs.line_from_high_pct > -8.0:
        score += 2.0

    if rs.line_rising:
        score += 3.0
        rs.notes.append("Force relative en hausse sur un mois")

    for value, weight in ((rs.rs_1m, 2.0), (rs.rs_3m, 2.5), (rs.rs_6m, 1.5)):
        if value > 10:
            score += weight
        elif value > 3:
            score += weight * 0.6
        elif value < -5:
            score -= weight * 0.5

    if rs.rs_3m < 0 and rs.rs_6m < 0:
        rs.notes.append("Sous-performance sur 3 et 6 mois — l'argent va ailleurs")

    rs.score = max(0.0, min(15.0, score))
    return rs


# --------------------------------------------------------------------------
# Régime de marché
# --------------------------------------------------------------------------
@dataclass
class MarketRegime:
    label: str = NEUTRE
    score: float = 50.0            # 0 à 100
    index_above_ma50: bool = False
    index_above_ma200: bool = False
    ma50_rising: bool = False
    drawdown_pct: float = 0.0      # écart au plus haut 52 semaines
    breadth_pct: float | None = None
    vix: float | None = None
    allow_new_positions: bool = True
    size_multiplier: float = 1.0
    notes: list[str] = field(default_factory=list)


def market_regime(index_bars: md.Bars | None, *, breadth_pct: float | None = None,
                  vix_bars: md.Bars | None = None) -> MarketRegime:
    reg = MarketRegime()
    if not index_bars or len(index_bars) < 60:
        reg.notes.append("Indice indisponible — régime supposé neutre")
        return reg

    close = index_bars.close
    last = close[-1]
    ma50 = md.sma(close, 50)
    ma200 = md.sma(close, 200) if len(close) >= 200 else None
    ma50_prev = md.sma(close[:-21], 50) if len(close) > 71 else None

    reg.index_above_ma50 = bool(ma50 and last > ma50)
    reg.index_above_ma200 = bool(ma200 and last > ma200)
    reg.ma50_rising = bool(ma50 and ma50_prev and ma50 > ma50_prev)

    year = close[-min(252, len(close)):]
    high = max(year)
    if high > 0:
        reg.drawdown_pct = (last / high - 1.0) * 100.0

    score = 50.0
    if reg.index_above_ma200:
        score += 15.0
        reg.notes.append("Indice au-dessus de sa moyenne 200 jours")
    else:
        score -= 20.0
        reg.notes.append("Indice sous sa moyenne 200 jours — contexte défensif")
    if reg.index_above_ma50:
        score += 10.0
    else:
        score -= 10.0
    if reg.ma50_rising:
        score += 8.0
    else:
        score -= 8.0

    if reg.drawdown_pct > -3:
        score += 8.0
    elif reg.drawdown_pct < -15:
        score -= 15.0
        reg.notes.append(f"Indice à {reg.drawdown_pct:.0f} % de son plus haut annuel")
    elif reg.drawdown_pct < -8:
        score -= 7.0

    if breadth_pct is not None:
        reg.breadth_pct = breadth_pct
        if breadth_pct >= 60:
            score += 10.0
            reg.notes.append(f"{breadth_pct:.0f} % des valeurs au-dessus de leur MM50")
        elif breadth_pct <= 30:
            score -= 12.0
            reg.notes.append(f"Seulement {breadth_pct:.0f} % des valeurs au-dessus de leur MM50")

    if vix_bars and len(vix_bars) > 0:
        reg.vix = vix_bars.close[-1]
        if reg.vix >= 30:
            score -= 12.0
            reg.notes.append(f"VIX à {reg.vix:.0f} — marché en stress")
        elif reg.vix >= 22:
            score -= 5.0
        elif reg.vix <= 15:
            score += 4.0

    reg.score = max(0.0, min(100.0, score))
    if reg.score >= 65:
        reg.label = RISK_ON
    elif reg.score <= 40:
        reg.label = RISK_OFF
    else:
        reg.label = NEUTRE

    if reg.label == RISK_OFF:
        reg.size_multiplier = 0.4
        reg.allow_new_positions = reg.score > 25
        if not reg.allow_new_positions:
            reg.notes.append("Régime trop dégradé — aucune nouvelle prise de risque")
    elif reg.label == NEUTRE:
        reg.size_multiplier = 0.7
    else:
        reg.size_multiplier = 1.0

    return reg


def breadth(above_ma50: int, total: int) -> float | None:
    """Pourcentage de valeurs au-dessus de leur MM50 — mesuré, jamais estimé."""
    if total <= 0:
        return None
    return above_ma50 / total * 100.0


# --------------------------------------------------------------------------
# Force ABSOLUE — sans indice de référence
# --------------------------------------------------------------------------
@dataclass
class AbsoluteStrength:
    """Force mesurée sur la valeur seule, sans la comparer à quoi que ce soit.

    La force relative punit mécaniquement toute action dont l'indice monte fort :
    à comportement identique, une valeur perdait près de 10 points sur 100 selon
    la vigueur de sa place de cotation. Une action qui suit un indice qui explose
    n'est pas plus faible qu'une action qui bat un indice à plat.

    On mesure donc aussi la force en soi : la distance au plus haut annuel, et un
    momentum rapporté à la volatilité propre du titre — deux quantités qui ne
    dépendent d'aucune référence extérieure et qui se calculent aussi bien dans
    le scan que dans le backtest.
    """
    from_high_pct: float = 0.0        # distance au plus haut 52 semaines
    change_3m: float = 0.0
    change_6m: float = 0.0
    risk_adjusted_3m: float = 0.0     # progression rapportée au bruit du titre
    risk_adjusted_6m: float = 0.0
    score: float = 0.0                # 0 à 10
    notes: list[str] = field(default_factory=list)
    available: bool = False


def absolute_strength(bars: md.Bars | None) -> AbsoluteStrength:
    out = AbsoluteStrength()
    if not bars or len(bars) < 130:
        out.notes.append("Historique trop court pour juger la force propre")
        return out

    close = bars.close
    price = close[-1]
    year = close[-min(252, len(close)):]
    high = max(year)
    if high > 0:
        out.from_high_pct = (price / high - 1.0) * 100.0

    out.change_3m = md.pct_change(close, 63) or 0.0
    out.change_6m = md.pct_change(close, 126) or 0.0

    # Le bruit propre du titre : ATR en % du prix, etendu sur la periode.
    atr_series = md.atr_pct_series(bars, 14)
    atr_pct = fmean(atr_series[-20:]) if len(atr_series) >= 20 else 0.0
    if atr_pct <= 0:
        # Un titre sans la moindre volatilite n'est pas une action calme, c'est
        # une cotation figee ou suspendue. On ne le note pas, on le signale.
        out.notes.append("Aucune volatilité mesurable — cotation figée ?")
        return out
    out.risk_adjusted_3m = out.change_3m / (atr_pct * sqrt(63))
    out.risk_adjusted_6m = out.change_6m / (atr_pct * sqrt(126))
    out.available = True

    score = 0.0
    # La proximite du plus haut ne compte QUE si le titre a réellement progressé.
    # Une ligne plate est en permanence a son plus haut annuel : sans cette
    # condition elle marquait 6 sur 10 en « force » sans avoir bougé d'un cent.
    if out.change_6m > 0:
        if out.from_high_pct > -5:
            score += 4.0
            out.notes.append(f"À {abs(out.from_high_pct):.1f} % de son plus haut annuel")
        elif out.from_high_pct > -15:
            score += 2.5
        elif out.from_high_pct > -25:
            score += 1.0
    elif out.from_high_pct < -25:
        out.notes.append(f"À {abs(out.from_high_pct):.0f} % sous son plus haut annuel")

    for value, label in ((out.risk_adjusted_3m, "3 mois"), (out.risk_adjusted_6m, "6 mois")):
        if value >= 1.0:
            score += 3.0
        elif value >= 0.4:
            score += 2.0
        elif value >= 0.0:
            score += 1.0
        else:
            score -= 1.0

    if out.risk_adjusted_3m >= 1.0:
        out.notes.append(f"Progression nette du bruit sur 3 mois ({out.risk_adjusted_3m:.1f}σ)")

    out.score = max(0.0, min(10.0, score))
    return out


# --------------------------------------------------------------------------
# Concentration
# --------------------------------------------------------------------------
def returns(bars: md.Bars | None, lookback: int = 126) -> list[float]:
    if not bars or len(bars) < 30:
        return []
    close = bars.close[-(lookback + 1):]
    out = []
    for older, newer in zip(close, close[1:]):
        out.append((newer / older - 1.0) if older > 0 else 0.0)
    return out


def correlation(a: list[float], b: list[float]) -> float | None:
    """Corrélation de Pearson sur les rendements quotidiens appariés."""
    n = min(len(a), len(b))
    if n < 40:
        return None
    a, b = a[-n:], b[-n:]
    ma, mb = fmean(a), fmean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = sqrt(sum((x - ma) ** 2 for x in a))
    db = sqrt(sum((y - mb) ** 2 for y in b))
    if da == 0 or db == 0:
        return None
    return num / (da * db)


def excess_returns(bars: md.Bars | None, bench: md.Bars | None,
                   lookback: int = 126) -> list[float]:
    """Rendements du titre MOINS ceux de son indice, appariés par date.

    La corrélation brute entre deux actions est dominée par le bêta de marché :
    deux valeurs sans le moindre rapport montent et descendent ensemble parce
    qu'elles suivent le même indice. Mesurée ainsi, la parenté était fictive et
    le plafond écartait presque tout le monde. En retirant le mouvement de
    l'indice, il ne reste que la co-variation propre — celle qui signale un vrai
    doublon économique.
    """
    if bench is None:
        return returns(bars, lookback)
    px, bx = align(bars, bench)
    if len(px) < 41:
        return []
    px, bx = px[-(lookback + 1):], bx[-(lookback + 1):]
    out = []
    for (po, pn), (bo, bn) in zip(zip(px, px[1:]), zip(bx, bx[1:])):
        r_stock = (pn / po - 1.0) if po > 0 else 0.0
        r_bench = (bn / bo - 1.0) if bo > 0 else 0.0
        out.append(r_stock - r_bench)
    return out


def limit_concentration(ranked, series, benchmarks=None, *, max_corr: float = 0.75,
                        max_per_cluster: int = 2):
    """Écarte les doublons économiques d'une sélection déjà classée.

    Trois banques ne font pas trois signaux : elles font un seul pari décliné
    trois fois. Le scanner Forex avait produit dix signaux qui étaient en
    réalité le même — « vendre une valeur refuge » — et aucun n'a fonctionné.

    Faute de données sectorielles fiables (Yahoo renvoie 401 sur les profils,
    EDGAR ne couvre que les États-Unis), la parenté est mesurée là où elle se
    voit vraiment : la corrélation des rendements quotidiens. Deux titres qui
    montent et descendent ensemble portent le même risque, quel que soit le
    secteur que leur attribue une base de données.

    La parenté est mesurée sur les rendements EXCÉDENTAIRES (titre moins son
    indice) : sans cela le bêta de marché ferait passer pour jumelles deux
    valeurs qui n'ont rien à voir.

    `ranked` est parcouru dans l'ordre : le mieux classé garde sa place.
    Renvoie (gardés, écartés) — jamais une liste tronquée en silence.
    """
    benchmarks = benchmarks or {}

    def serie(candidate):
        ticker = getattr(candidate, "ticker", "")
        return excess_returns(series.get(ticker), benchmarks.get(ticker))

    kept, dropped = [], []
    clusters: list[list] = []
    for candidate in ranked:
        mine = serie(candidate)
        placed = False
        for cluster in clusters:
            corr = None
            for member in cluster:
                value = correlation(mine, serie(member))
                if value is not None and value >= max_corr:
                    corr = value
                    break
            if corr is None:
                continue
            placed = True
            if len(cluster) < max_per_cluster:
                cluster.append(candidate)
                kept.append(candidate)
            else:
                dropped.append((candidate, corr, cluster[0]))
            break
        if not placed:
            clusters.append([candidate])
            kept.append(candidate)
    return kept, dropped
