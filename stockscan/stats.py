"""Statistiques de performance — en R, avec leur incertitude.

Un résultat sans intervalle de confiance n'est pas un résultat. Sur trente
trades, une espérance de +0,3R peut parfaitement être du bruit ; le dire est
plus utile que de l'afficher en gras.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import sqrt


@dataclass
class Performance:
    trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    expectancy: float = 0.0        # en R, par trade
    total_r: float = 0.0
    profit_factor: float = 0.0
    std_dev: float = 0.0
    std_error: float = 0.0
    ci95: tuple[float, float] = (0.0, 0.0)
    max_drawdown_r: float = 0.0
    significant: bool = False      # l'intervalle exclut-il zéro ?

    @property
    def verdict(self) -> str:
        if self.trades < 30:
            return "ÉCHANTILLON INSUFFISANT"
        if not self.significant:
            return "INDISCERNABLE DE ZÉRO"
        return "POSITIF" if self.expectancy > 0 else "NÉGATIF"


def breakeven_win_rate(reward_risk: float) -> float:
    """Taux de réussite minimal pour ne rien perdre, à R:R donné."""
    return 0.0 if reward_risk <= 0 else 1.0 / (1.0 + reward_risk) * 100.0


def max_drawdown(r_multiples: list[float]) -> float:
    peak = 0.0
    equity = 0.0
    worst = 0.0
    for r in r_multiples:
        equity += r
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def performance(r_multiples: list[float]) -> Performance:
    out = Performance(trades=len(r_multiples))
    if not r_multiples:
        return out

    out.wins = sum(1 for r in r_multiples if r > 0)
    out.losses = out.trades - out.wins
    out.win_rate = out.wins / out.trades * 100.0
    out.total_r = sum(r_multiples)
    out.expectancy = out.total_r / out.trades

    gains = sum(r for r in r_multiples if r > 0)
    pains = -sum(r for r in r_multiples if r < 0)
    out.profit_factor = float("inf") if pains == 0 and gains > 0 else (
        0.0 if pains == 0 else gains / pains)

    if out.trades > 1:
        mean = out.expectancy
        var = sum((r - mean) ** 2 for r in r_multiples) / (out.trades - 1)
        out.std_dev = sqrt(var)
        out.std_error = out.std_dev / sqrt(out.trades)
        half = 1.96 * out.std_error
        out.ci95 = (round(mean - half, 4), round(mean + half, 4))
        out.significant = out.ci95[0] > 0 or out.ci95[1] < 0

    out.max_drawdown_r = max_drawdown(r_multiples)
    return out


def summarise(perf: Performance, label: str = "") -> str:
    if perf.trades == 0:
        return f"{label}: aucun trade"
    pf = "∞" if perf.profit_factor == float("inf") else f"{perf.profit_factor:.3f}"
    return (f"{label}: {perf.trades} trades · réussite {perf.win_rate:.1f} % · "
            f"espérance {perf.expectancy:+.3f}R · PF {pf} · "
            f"IC95 [{perf.ci95[0]:+.3f} ; {perf.ci95[1]:+.3f}] · "
            f"pire série {perf.max_drawdown_r:.2f}R · {perf.verdict}")
