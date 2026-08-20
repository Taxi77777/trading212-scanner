"""Notation sur 100, et un score PRÉ-CASSURE séparé.

Deux scores, parce qu'ils répondent à deux questions différentes :

  * le score global dit « cette action est-elle en bonne santé technique ? » ;
  * le score pré-cassure dit « est-elle sur le point de casser, maintenant ? ».

Une action peut être excellente et déjà partie : score global 85, pré-cassure 12.
C'est exactement le cas que le §21 demande de pénaliser. Les mélanger dans un
seul chiffre effacerait l'information qui compte le plus ici.

Chaque composante renvoie ses points ET la raison de ces points (§38) : aucun
score ne doit être un nombre tombé du ciel.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import structure as st
from . import strength as sg

GRADE_A, GRADE_B, GRADE_C, GRADE_D = "A", "B", "C", "D"


@dataclass
class Component:
    key: str
    label: str
    raw: float
    maximum: float
    weight: float
    points: float = 0.0
    why: list[str] = field(default_factory=list)

    @property
    def pct(self) -> float:
        return 0.0 if self.maximum <= 0 else self.raw / self.maximum * 100.0


@dataclass
class Score:
    total: float = 0.0
    prebreakout: float = 0.0
    grade: str = GRADE_D
    components: list[Component] = field(default_factory=list)
    penalties: list[tuple[str, float]] = field(default_factory=list)
    bonuses: list[tuple[str, float]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def component(self, key: str) -> Component | None:
        for c in self.components:
            if c.key == key:
                return c
        return None

    def explain(self) -> list[str]:
        lines = [f"{c.label} : {c.points:.1f}/{c.weight:.0f}" for c in self.components]
        lines += [f"Pénalité {name} : -{value:.1f}" for name, value in self.penalties]
        lines += [f"Bonus {name} : +{value:.1f}" for name, value in self.bonuses]
        return lines


# Poids en points du score final. La somme fait 100 — vérifié par un test.
WEIGHTS = {
    "trend": 15.0,
    "base": 18.0,
    # Les 20 points de force sont SCINDES. La force relative punit mecaniquement
    # toute action dont l'indice monte fort : a comportement identique, une
    # valeur perdait pres de 10 points sur 100 selon la vigueur de sa place.
    # La moitie des points va donc a une mesure sans reference.
    "relative": 10.0,
    "absolute": 10.0,
    "volume": 15.0,
    "accumulation": 14.0,
    "compression": 10.0,
    "proximity": 8.0,
}

LABELS = {
    "trend": "Tendance de fond",
    "base": "Structure / base",
    "relative": "Force relative",
    "absolute": "Force propre",
    "volume": "Volume",
    "accumulation": "Accumulation",
    "compression": "Compression",
    "proximity": "Proximité résistance",
}


def grade_for(total: float) -> str:
    """Seuils de classement. Isolés pour être testables sans fabriquer un score."""
    if total >= 75:
        return GRADE_A
    if total >= 60:
        return GRADE_B
    if total >= 45:
        return GRADE_C
    return GRADE_D


def proximity_score(resistance: st.Resistance | None, price: float,
                    pivot: float | None = None) -> tuple[float, list[str]]:
    """0 à 10. Le maximum est atteint juste sous une résistance éprouvée.

    Loin de la résistance il n'y a rien à anticiper ; au-dessus, ce n'est plus
    une pré-cassure mais une cassure — un autre cas, traité ailleurs.
    """
    level = pivot if (pivot is not None and pivot > 0) else (
        resistance.level if resistance is not None else 0.0)
    if level <= 0 or price <= 0:
        return 0.0, ["Aucune résistance identifiée au-dessus du cours"]
    dist = (level / price - 1.0) * 100.0
    why: list[str] = []
    if dist < 0:
        return 2.0, [f"Cours déjà au-dessus du niveau {level:.2f}"]
    if dist <= 3:
        base = 10.0
        why.append(f"À {dist:.1f} % de la résistance {level:.2f} — collée dessous")
    elif dist <= 6:
        base = 8.0
        why.append(f"À {dist:.1f} % de la résistance {level:.2f}")
    elif dist <= 10:
        base = 5.0
        why.append(f"À {dist:.1f} % de la résistance — encore du chemin")
    elif dist <= 18:
        base = 2.0
        why.append(f"À {dist:.1f} % de la résistance — trop loin pour anticiper")
    else:
        base = 0.0
        why.append(f"À {dist:.1f} % de la résistance — hors sujet")
    quality_bonus = min(1.5, (resistance.quality if resistance else 0.0) / 10.0 * 1.5)
    if resistance is not None and resistance.tests >= 3:
        why.append(f"Niveau testé {resistance.tests} fois — il compte")
    return min(10.0, base * 0.85 + quality_bonus), why


def prebreakout_score(*, base: st.Base, volume: st.VolumeProfile,
                      accum: st.Accumulation, comp: st.Compression,
                      resistance: st.Resistance | None, price: float,
                      ext: st.Extension,
                      pivot: float | None = None) -> tuple[float, list[str]]:
    """0 à 100 — la probabilité que la cassure soit proche, pas qu'elle réussisse."""
    why: list[str] = []
    total = 0.0

    total += comp.score / 5.0 * 25.0
    if comp.detected:
        why.append(f"Volatilité comprimée (ATR {comp.ratio:.2f}× sa référence)")

    prox, prox_why = proximity_score(resistance, price, pivot)
    total += prox / 10.0 * 25.0
    why += prox_why

    if volume.dry_up:
        total += 15.0
        why.append("Volume asséché dans la base — les vendeurs sont épuisés")
    elif 0.0 < volume.ratio20 < 1.1:
        # Le `0.0 <` compte : un VolumeProfile vide a ratio20 = 0, ce qui
        # satisfaisait « < 1,1 » et offrait 7 points à une valeur dont on n'a
        # aucune donnée de volume. L'absence de mesure ne vaut pas une mesure
        # favorable.
        total += 7.0

    total += accum.score / 10.0 * 20.0
    if accum.obv_near_high and accum.price_below_resistance:
        why.append("OBV sur ses sommets alors que le cours reste sous la résistance")

    total += min(15.0, base.quality / 15.0 * 15.0)
    if base.found:
        why.append(f"{base.label} de {base.length} séances, amplitude {base.width_pct:.1f} %")
    else:
        why.append("Aucune base identifiable — rien à casser")

    if ext.disqualifies_prebreakout:
        why.append("Le titre a déjà couru : ce n'est plus une pré-cassure")
        return 0.0, why
    total -= ext.penalty

    return max(0.0, min(100.0, total)), why


def score(*, trend: st.TrendState, base: st.Base, volume: st.VolumeProfile,
          accum: st.Accumulation, comp: st.Compression,
          rs: sg.RelativeStrength, resistance: st.Resistance | None,
          ext: st.Extension, price: float,
          absolute: sg.AbsoluteStrength | None = None,
          pivot: float | None = None,
          sector_rs: sg.RelativeStrength | None = None,
          fundamental: float | None = None) -> Score:
    out = Score()

    prox, prox_why = proximity_score(resistance, price, pivot)
    raw = {
        "trend": (trend.score, 10.0, list(trend.notes)),
        "base": (base.quality, 15.0, list(base.notes)),
        "relative": (rs.score, 15.0, list(rs.notes)),
        "absolute": ((absolute.score if absolute else 0.0), 10.0,
                     list(absolute.notes) if absolute else
                     ["Force propre non calculée"]),
        "volume": (volume.score, 15.0, list(volume.notes)),
        "accumulation": (accum.score, 10.0, list(accum.notes)),
        "compression": (comp.score, 5.0, list(comp.notes)),
        "proximity": (prox, 10.0, prox_why),
    }

    total = 0.0
    for key, weight in WEIGHTS.items():
        value, maximum, why = raw[key]
        points = 0.0 if maximum <= 0 else max(0.0, min(1.0, value / maximum)) * weight
        out.components.append(Component(key, LABELS[key], value, maximum, weight, points, why))
        total += points

    if ext.penalty > 0:
        out.penalties.append(("extension", ext.penalty))
        total -= ext.penalty
        out.notes += ext.notes

    if trend.direction == st.BAISSIERE:
        out.penalties.append(("tendance baissière", 10.0))
        total -= 10.0
        out.notes.append("Tendance de fond baissière — on ne devance pas une cassure ici")

    if accum.negative_divergence:
        out.penalties.append(("divergence OBV négative", 6.0))
        total -= 6.0

    if sector_rs is not None and sector_rs.available:
        if sector_rs.rs_3m > 3:
            out.bonuses.append(("secteur porteur", 4.0))
            total += 4.0
            out.notes.append(f"Secteur en avance de {sector_rs.rs_3m:.1f} pts sur l'indice")
        elif sector_rs.rs_3m < -5:
            out.penalties.append(("secteur à la traîne", 4.0))
            total -= 4.0
            out.notes.append(f"Secteur en retard de {abs(sector_rs.rs_3m):.1f} pts sur l'indice")

    if fundamental is not None:
        bonus = max(-5.0, min(5.0, (fundamental - 5.0)))
        if bonus > 0:
            out.bonuses.append(("fondamentaux", bonus))
        elif bonus < 0:
            out.penalties.append(("fondamentaux", -bonus))
        total += bonus

    out.total = max(0.0, min(100.0, total))
    out.prebreakout, pre_why = prebreakout_score(
        base=base, volume=volume, accum=accum, comp=comp,
        resistance=resistance, price=price, ext=ext, pivot=pivot)
    out.notes += pre_why

    out.grade = grade_for(out.total)
    return out
