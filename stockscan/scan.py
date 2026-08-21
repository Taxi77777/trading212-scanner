"""Orchestration du scan : données -> structure -> score -> phase -> plan -> alerte.

Budget de requêtes : une seule série journalière de 5 ans par valeur, plus un
indice par place. Tout le reste (hebdomadaire, moyennes, ATR, OBV) est dérivé de
cette série. Environ 560 appels pour 553 valeurs — pas 3 000.

Deux passes, parce que le régime dépend de l'ensemble :

  passe 1  chaque valeur est mesurée isolément ;
  passe 2  la respiration du marché (part des valeurs au-dessus de leur MM50)
           est connue, le régime est calculé, et seulement alors les plans de
           risque sont dimensionnés.

Calculer le régime avant d'avoir scanné reviendrait à le deviner.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from . import ai_judge
from . import fundamentals as fu
from . import market_data as md
from . import phases as ph
from . import scoring as sc
from . import strength as sg
from . import structure as st
from . import universe as uni
from .candidate import Candidate, ScanSummary

VIX = "^VIX"

# Priorité d'affichage : la pré-cassure d'abord, c'est tout l'objet du système.
PHASE_ORDER = {ph.PRE_BREAKOUT: 0, ph.RETEST: 1, ph.BREAKOUT: 2, ph.EARLY: 3}
ACTIONABLE = tuple(PHASE_ORDER)


@dataclass
class Config:
    markets: tuple[str, ...] | None = None
    limit: int | None = None
    top: int = 5
    min_score: float = 55.0
    min_prebreakout: float = 45.0
    min_rr: float = 2.0
    account_risk_pct: float = 1.0
    workers: int = 6
    per_second: float = 6.0
    use_ai: bool = True
    use_fundamentals: bool = True
    max_correlation: float = 0.75
    max_per_cluster: int = 2


def _benchmark_bars(data: md.MarketData, symbols) -> dict[str, md.Bars]:
    # Liste figée : apparier un `set` avec le résultat de `map` suppose que le
    # set s'itère deux fois dans le même ordre. C'est vrai aujourd'hui, ce n'est
    # pas une garantie du langage, et un décalage ici associerait une valeur au
    # mauvais indice sans rien casser visiblement.
    ordered = sorted(symbols)
    out: dict[str, md.Bars] = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for symbol, bars in zip(ordered, pool.map(data.daily, ordered)):
            if bars is not None and len(bars) > 60:
                out[symbol] = bars
    return out


def measure(stock: uni.Stock, bars: md.Bars, bench: md.Bars | None,
            market: uni.Market) -> Candidate:
    """Passe 1 : tout ce qui se calcule sans connaître le reste du marché."""
    # Nom et devise viennent de Yahoo quand ils sont la : « REC » ne dit rien a
    # personne, et une valeur londonienne est cotee en PENCE, pas en livres.
    c = Candidate(ticker=stock.ticker, name=bars.name or stock.ticker,
                  symbol=stock.symbol, market=market.code,
                  market_label=market.label,
                  currency=bars.currency or market.currency,
                  benchmark_label=market.index_label, price=bars.close[-1])
    c.trend = st.trend(bars)
    c.base = st.detect_base(bars)
    c.resistance = st.nearest_above(st.find_resistances(bars), c.price)
    c.volume = st.volume_profile(bars, c.base)
    c.accum = st.accumulation(bars, c.resistance)
    c.comp = st.compression(bars)
    c.ext = st.extension(bars)
    c.rs = sg.relative_strength(bars, bench)
    c.absolute = sg.absolute_strength(bars)
    c.phase = ph.classify(bars, base=c.base, resistance=c.resistance, comp=c.comp,
                          volume=c.volume, accum=c.accum, ext=c.ext, trend=c.trend)
    # Le score note la proximite depuis le MEME niveau que la phase.
    pivot = ph.pivot_level(c.base, c.resistance)
    c.score = sc.score(trend=c.trend, base=c.base, volume=c.volume, accum=c.accum,
                       comp=c.comp, rs=c.rs, absolute=c.absolute,
                       resistance=c.resistance, ext=c.ext, price=c.price,
                       pivot=pivot)
    return c


def _above_ma50(bars: md.Bars) -> bool:
    ma = md.sma(bars.close, 50)
    return bool(ma and bars.close[-1] > ma)


def rank_key(c: Candidate):
    """Pré-cassure d'abord, puis probabilité de cassure, puis qualité générale."""
    return (PHASE_ORDER.get(c.phase.name, 9), -c.score.prebreakout, -c.score.total)


def run(config: Config | None = None, *, data: md.MarketData | None = None,
        now: str | None = None) -> tuple[ScanSummary, list[Candidate]]:
    cfg = config or Config()
    started = time.monotonic()
    data = data or md.MarketData(per_second=cfg.per_second)

    stocks = uni.universe(cfg.markets)
    if cfg.limit:
        stocks = stocks[:cfg.limit]
    wanted = {s.market.index_symbol for s in stocks} | {VIX}
    bench = _benchmark_bars(data, wanted)

    summary = ScanSummary(date=now or time.strftime("%d/%m/%Y"), analysed=len(stocks))

    def one(stock: uni.Stock):
        bars = data.daily(stock.symbol)
        if bars is None or len(bars) < 210:
            return stock, None, None
        bench_bars = bench.get(stock.market.index_symbol)
        return stock, bars, measure(stock, bars, bench_bars, stock.market)

    measured: list[tuple[uni.Stock, md.Bars, Candidate]] = []
    with ThreadPoolExecutor(max_workers=cfg.workers) as pool:
        for stock, bars, cand in pool.map(one, stocks):
            if cand is None:
                summary.failed += 1
            else:
                measured.append((stock, bars, cand))
    summary.fetched = len(measured)

    # Passe 2 : le marché, mesuré et non supposé.
    regimes: dict[str, sg.MarketRegime] = {}
    for code in {s.market.code for s, _b, _c in measured}:
        rows = [b for s, b, _c in measured if s.market.code == code]
        pct = sg.breadth(sum(1 for b in rows if _above_ma50(b)), len(rows))
        regimes[code] = sg.market_regime(bench.get(uni.MARKETS[code].index_symbol),
                                         breadth_pct=pct, vix_bars=bench.get(VIX))

    for _s, bars, c in measured:
        c.regime = regimes.get(c.market)
        summary.counts[c.phase.name] = summary.counts.get(c.phase.name, 0) + 1
        c.plan = ph.risk_plan(bars, phase=c.phase, base=c.base, resistance=c.resistance,
                              regime=c.regime, account_risk_pct=cfg.account_risk_pct,
                              min_rr=cfg.min_rr)

    keep = [c for _s, _b, c in measured
            if c.phase.name in ACTIONABLE and c.plan.tradeable
            and c.score.total >= cfg.min_score
            and (c.phase.name != ph.PRE_BREAKOUT or c.score.prebreakout >= cfg.min_prebreakout)]
    keep.sort(key=rank_key)

    # Trois banques ne font pas trois signaux. On ecarte les doublons
    # economiques AVANT de couper au top N, sinon la coupe garderait
    # mecaniquement le meme pari repete.
    series = {c.ticker: b for _s, b, c in measured}
    # L'indice de chaque valeur, pour retirer le beta de marche : sans cela
    # deux titres sans rapport paraissent jumeaux parce qu'ils suivent la meme
    # place, et le plafond ecarte presque tout le monde.
    marches = {c.ticker: bench.get(s.market.index_symbol)
               for s, _b, c in measured}
    keep, dropped = sg.limit_concentration(keep, series, marches,
                                           max_corr=cfg.max_correlation,
                                           max_per_cluster=cfg.max_per_cluster)
    for candidate, corr, kept_first in dropped:
        summary.notes.append(
            f"{candidate.ticker} écartée — corrélée à {corr:.2f} avec "
            f"{kept_first.ticker}, déjà retenue")
    summary.dropped_correlated = len(dropped)
    shortlist = keep[:cfg.top]

    if cfg.use_fundamentals:
        _apply_fundamentals(shortlist)
    if cfg.use_ai and ai_judge.configured():
        _apply_ai(shortlist)

    final = [c for c in shortlist if not c.blocked_by_ai]
    summary.blocked_by_ai = len(shortlist) - len(final)
    summary.kept = len(final)
    summary.duration_s = round(time.monotonic() - started, 1)

    primary = regimes.get("US") or (next(iter(regimes.values())) if regimes else None)
    summary.regime = primary
    if primary is not None:
        code = "US" if "US" in regimes else next(iter(regimes))
        summary.regime_label_market = uni.MARKETS[code].index_label
    summary.notes.append(
        f"{data.stats['calls']} requêtes, {data.stats['ok']} réponses utiles")
    return summary, final


def _apply_fundamentals(candidates: list[Candidate]) -> None:
    """Uniquement les valeurs américaines : EDGAR ne couvre pas l'Europe."""
    us = [c for c in candidates if c.market == "US"]
    if not us:
        return
    client = fu.SecClient()
    for c in us:
        c.fundamental = client.fundamentals(c.ticker)
        if c.fundamental.available and c.fundamental.score is not None:
            c.score = sc.score(trend=c.trend, base=c.base, volume=c.volume,
                               accum=c.accum, comp=c.comp, rs=c.rs,
                               absolute=c.absolute, resistance=c.resistance,
                               ext=c.ext, price=c.price,
                               pivot=ph.pivot_level(c.base, c.resistance),
                               fundamental=c.fundamental.score)


def _apply_ai(candidates: list[Candidate]) -> None:
    for c in candidates:
        c.ai = ai_judge.judge_candidate(c)
