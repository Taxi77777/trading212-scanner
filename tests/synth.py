"""Séries synthétiques déterministes pour tester le moteur hors ligne."""
import math

from stockscan.market_data import Bars

DAY = 86400
T0 = 1_700_000_000


def build(closes, volumes=None, spread=0.01, t0=T0, step=DAY):
    """Barres synthetiques dont l'amplitude intrabar suit le mouvement reel.

    Une meche de taille fixe (ancienne version) donnait le meme ATR a une base
    serree et a une tendance agitee : le test de compression devenait aveugle
    parce que le range intrabar constant ecrasait le vrai mouvement. Ici
    l'ouverture est la cloture precedente et la meche est proportionnelle au
    deplacement, avec un plancher de bruit. C'est ainsi que se comportent de
    vraies barres.
    """
    n = len(closes)
    vols = volumes or [1_000_000.0] * n
    ts = [t0 + i * step for i in range(n)]
    floor = spread * 0.1
    op, hi, lo = [], [], []
    for i, c in enumerate(closes):
        prev = closes[i - 1] if i else c * (1 - spread * 0.2)
        move = abs(c - prev) / prev if prev else 0.0
        wick = floor + 0.5 * move
        op.append(prev)
        hi.append(max(prev, c) * (1 + wick))
        lo.append(min(prev, c) * (1 - wick))
    return Bars(ts, op, hi, lo, list(closes), list(vols))


def uptrend(n=400, start=50.0, daily=0.0018, wobble=0.006):
    return [start * (1 + daily) ** i * (1 + wobble * math.sin(i / 6)) for i in range(n)]


def downtrend(n=400, start=120.0, daily=-0.0015, wobble=0.006):
    return [start * (1 + daily) ** i * (1 + wobble * math.sin(i / 6)) for i in range(n)]


BASE_DISCOUNT = 0.035          # la base s'installe 3,5 % sous le sommet du rallye


def base_level(top=90.0):
    """Plafond de la base construite par rally_then_flat_base."""
    return top * (1 - BASE_DISCOUNT)


def rally_then_flat_base(rally=200, base=60, start=40.0, top=90.0, tight=0.004,
                         rally_wobble=0.030):
    """Hausse franche puis base plate juste sous le sommet — le cas du §11.

    La hausse doit être bruitée : une rampe parfaitement linéaire a un ATR quasi
    nul, ce qui rendrait la base « plus volatile » que la tendance et inverserait
    le test de compression. Ce n'est pas ce que fait un vrai marché.
    """
    out = []
    for i in range(rally):
        trend_price = start + (top - start) * (i / rally)
        out.append(trend_price * (1 + rally_wobble * math.sin(i / 5.0)
                                  + rally_wobble * 0.6 * math.sin(i / 1.7)))
    # Transition progressive : un vrai titre ne saute pas du sommet au plancher
    # de sa base en une seance. Un saut d'un seul chandelier creerait une meche
    # enorme qui deviendrait, a tort, le sommet de la base.
    level = base_level(top)
    bridge = min(5, base)
    last = out[-1] if out else level
    for i in range(1, bridge + 1):
        out.append(last + (level - last) * i / bridge)
    for i in range(base - bridge):
        out.append(level * (1 + tight * math.sin(i / 4.5)))
    return out


def tightening_base(rally=220, base=70, start=40.0, top=90.0, depth=0.10,
                    rally_wobble=0.030, decay=0.88):
    """Base realiste : profonde au debut, qui se resserre vers la fin.

    Les bases reelles font 8 a 15 % de profondeur, pas 1 %. Une base minuscule
    donne un objectif minuscule et donc un R:R proche de 1 quel que soit le
    systeme — ce qui teste la geometrie du triangle, pas le moteur. Ici
    l'amplitude fond progressivement : l'objectif reste la hauteur totale de la
    base, tandis que l'ATR recent (donc le stop) devient serre.
    """
    out = []
    for i in range(rally):
        trend_price = start + (top - start) * (i / rally)
        out.append(trend_price * (1 + rally_wobble * math.sin(i / 5.0)
                                  + rally_wobble * 0.6 * math.sin(i / 1.7)))
    level = base_level(top)
    bridge = min(5, base)
    last = out[-1] if out else level
    for i in range(1, bridge + 1):
        out.append(last + (level - last) * i / bridge)
    for i in range(base - bridge):
        shrink = 1.0 - (i / max(1, base - bridge)) * decay
        out.append(level * (1 + depth * 0.5 * shrink * math.sin(i / 5.0)))
    return out


def base_then_breakout(rally=200, base=60, after=8, start=40.0, top=90.0):
    """La cassure se mesure par rapport au sommet de la BASE, pas au sommet
    du rallye : c'est la base qui fait office de resistance immediate."""
    out = rally_then_flat_base(rally, base, start, top)
    pivot = base_level(top)
    for i in range(after):
        out.append(pivot * (1.018 + 0.006 * i))
    return out


def breakout_then_retest(rally=200, base=60, start=40.0, top=90.0):
    out = rally_then_flat_base(rally, base, start, top)
    pivot = base_level(top)
    out += [pivot * 1.020, pivot * 1.045, pivot * 1.055]   # cassure
    out += [pivot * 1.030, pivot * 1.008, pivot * 1.012]   # retour sur le pivot
    return out


def volumes_for(closes, base_vol=1_000_000.0, dry_from=None, surge_last=0):
    """Volume constructif : soutenu en hausse, asséché dans la base, explosif à la cassure."""
    out = []
    for i, c in enumerate(closes):
        prev = closes[i - 1] if i else c
        v = base_vol * (1.25 if c > prev else 0.8)
        if dry_from is not None and i >= dry_from:
            v *= 0.6
        out.append(v)
    for k in range(1, surge_last + 1):
        if k <= len(out):
            out[-k] *= 2.4
    return out


def deep_tightening_base(**kw):
    """Base profonde qui se resserre fort : le cas ou l'objectif paie le risque.

    L'objectif vient de la HAUTEUR de la base ; le stop, de l'agitation RECENTE.
    Quand une base profonde se calme vraiment, les deux divergent et le R:R
    depasse 2. Sans une telle fixture, on ne peut prouver que le filtre `min_rr`
    sait accepter — seulement qu'il sait refuser, ce qui ne demontre rien.
    """
    kw.setdefault("depth", 0.22)
    kw.setdefault("decay", 0.96)
    return tightening_base(**kw)
