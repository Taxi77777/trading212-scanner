"""Le candidat : tout ce que le moteur a établi sur une valeur, en un objet.

Aucun calcul ici — seulement le regroupement. Les modules qui écrivent
(Telegram, IA, backtest) lisent cet objet et rien d'autre, ce qui garantit que
le message envoyé et le score calculé parlent bien de la même chose.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import phases as ph
from . import scoring as sc
from . import strength as sg
from . import structure as st


@dataclass
class Candidate:
    ticker: str = ""
    symbol: str = ""
    market: str = ""
    market_label: str = ""
    currency: str = ""
    benchmark_label: str = ""
    price: float = 0.0

    trend: st.TrendState = field(default_factory=st.TrendState)
    base: st.Base = field(default_factory=st.Base)
    resistance: st.Resistance | None = None
    volume: st.VolumeProfile = field(default_factory=st.VolumeProfile)
    accum: st.Accumulation = field(default_factory=st.Accumulation)
    comp: st.Compression = field(default_factory=st.Compression)
    ext: st.Extension = field(default_factory=st.Extension)
    rs: sg.RelativeStrength = field(default_factory=sg.RelativeStrength)
    regime: sg.MarketRegime | None = None
    fundamental: Any = None

    score: sc.Score = field(default_factory=sc.Score)
    phase: ph.Phase = field(default_factory=ph.Phase)
    plan: ph.RiskPlan = field(default_factory=ph.RiskPlan)
    ai: dict[str, Any] = field(default_factory=dict)

    @property
    def distance_pct(self) -> float:
        if not self.resistance or self.price <= 0:
            return 0.0
        return (self.resistance.level / self.price - 1.0) * 100.0

    @property
    def blocked_by_ai(self) -> bool:
        """Seule une contradiction réellement analysée bloque (jamais une panne)."""
        return bool(self.ai.get("available") and self.ai.get("contradiction"))


@dataclass
class ScanSummary:
    date: str = ""
    analysed: int = 0
    fetched: int = 0
    failed: int = 0
    regime: sg.MarketRegime | None = None
    regime_label_market: str = ""
    counts: dict[str, int] = field(default_factory=dict)
    kept: int = 0
    blocked_by_ai: int = 0
    duration_s: float = 0.0
    notes: list[str] = field(default_factory=list)
