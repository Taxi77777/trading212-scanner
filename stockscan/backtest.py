"""Backtest du scanner — sans regard vers l'avenir, et découpé en périodes.

Deux pièges évités ici :

1. **Le regard vers l'avenir.** À chaque date testée, le moteur ne voit que
   `bars.head(i + 1)`. Aucune moyenne, aucune base, aucune résistance n'est
   calculée sur des séances qui n'existaient pas encore.

2. **Le sur-ajustement.** Les résultats sont séparés en apprentissage,
   validation et hors échantillon (§34). Un système qui ne tient que sur la
   première moitié des données n'a rien démontré.

Les ambiguïtés intra-séance sont tranchées CONTRE le trade : si une bougie
touche à la fois le stop et l'objectif, la perte est comptée. Sans ce choix,
tout backtest sur données journalières s'embellit tout seul.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import market_data as md
from . import phases as ph
from . import scoring as sc
from . import strength as sg
from . import structure as st
from .stats import Performance, performance, summarise

SLIPPAGE_PCT = 0.10          # frais + écart, aller ET retour, en % du prix
TRAIN, VALIDATION, OUT_OF_SAMPLE = "apprentissage", "validation", "hors échantillon"
PERIODS = (TRAIN, VALIDATION, OUT_OF_SAMPLE)


@dataclass
class Trade:
    ticker: str = ""
    phase: str = ""
    entry_index: int = 0
    entry_ts: int = 0                # horodatage reel, comparable entre valeurs
    exit_ts: int = 0
    entry: float = 0.0
    stop: float = 0.0
    target: float = 0.0
    exit_index: int = 0
    exit: float = 0.0
    r_multiple: float = 0.0
    bars_held: int = 0
    outcome: str = ""            # GAGNE / PERDU / TEMPS / NON_DECLENCHE
    period: str = ""


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    by_phase: dict[str, Performance] = field(default_factory=dict)
    by_period: dict[str, Performance] = field(default_factory=dict)
    overall: Performance = field(default_factory=Performance)
    evaluated: int = 0            # nombre de dates examinées
    triggered: int = 0
    notes: list[str] = field(default_factory=list)

    def report(self) -> list[str]:
        lines = [summarise(self.overall, "GLOBAL")]
        lines += [summarise(p, f"phase {name}") for name, p in sorted(self.by_phase.items())]
        lines += [summarise(self.by_period[name], f"période {name}")
                  for name in PERIODS if name in self.by_period]
        lines += self.notes
        return lines


def _period_of(index: int, first: int, last: int) -> str:
    """Position dans la plage RÉELLEMENT testable, pas dans l'historique brut.

    Les `warmup` premières séances servent à amorcer les moyennes et les
    `max_bars` dernières n'ont pas assez de futur pour clore un trade : aucune
    des deux ne peut produire de trade. Les compter dans le découpage décalait
    tout — un 50/25/25 annoncé devenait 39/34/27 en pratique.
    """
    span = last - first
    ratio = (index - first) / span if span > 0 else 0.0
    if ratio < 0.5:
        return TRAIN
    if ratio < 0.75:
        return VALIDATION
    return OUT_OF_SAMPLE


def simulate(bars: md.Bars, start: int, entry: float, stop: float, target: float,
             *, max_bars: int = 60, trigger_bars: int = 10) -> tuple[str, int, float]:
    """Déroule les séances suivantes. Renvoie (issue, index de sortie, R).

    L'entrée est un ordre stop : elle ne se déclenche que si le cours va la
    chercher. Un plan jamais déclenché n'est pas un trade — le compter comme
    nul fausserait l'espérance dans les deux sens.
    """
    risk = entry - stop
    if risk <= 0:
        return "NON_DECLENCHE", start, 0.0

    fired = None
    for i in range(start + 1, min(start + 1 + trigger_bars, len(bars))):
        if bars.high[i] >= entry:
            fired = i
            break
    if fired is None:
        return "NON_DECLENCHE", start, 0.0

    cost = entry * SLIPPAGE_PCT / 100.0
    for i in range(fired, min(fired + max_bars, len(bars))):
        touches_stop = bars.low[i] <= stop
        touches_target = bars.high[i] >= target
        if touches_stop:                      # ambiguïté tranchée contre le trade
            return "PERDU", i, (stop - entry - cost) / risk
        if touches_target:
            return "GAGNE", i, (target - entry - cost) / risk
    last = min(fired + max_bars, len(bars)) - 1
    return "TEMPS", last, (bars.close[last] - entry - cost) / risk


def walk(bars: md.Bars, bench: md.Bars | None, *, ticker: str = "",
         step: int = 5, warmup: int = 260, max_bars: int = 60,
         min_rr: float = 2.0, min_score: float = 55.0,
         cooldown: int = 20) -> list[Trade]:
    """Rejoue l'historique séance par séance, moteur complet, sans triche.

    Le filtre de score est appliqué comme en production. Backtester la phase
    seule mesurerait un système que personne n'utilise.

    La force relative n'introduit pas de regard vers l'avenir : `align` parcourt
    les séances de l'action, donc les séances d'indice postérieures à la date
    testée ne sont jamais consultées.
    """
    trades: list[Trade] = []
    total = len(bars)
    if total < warmup + 80:
        return trades

    last_entry = -10 ** 9
    first_testable, last_testable = warmup, total - max_bars
    for i in range(first_testable, last_testable, step):
        if i - last_entry < cooldown:
            continue
        seen = bars.head(i + 1)
        base = st.detect_base(seen)
        resistance = st.nearest_above(st.find_resistances(seen), seen.close[-1])
        comp = st.compression(seen)
        volume = st.volume_profile(seen, base)
        accum = st.accumulation(seen, resistance)
        ext = st.extension(seen)
        trend = st.trend(seen)
        phase = ph.classify(seen, base=base, resistance=resistance, comp=comp,
                            volume=volume, accum=accum, ext=ext, trend=trend)
        if phase.name not in (ph.PRE_BREAKOUT, ph.BREAKOUT, ph.RETEST):
            continue
        rs = sg.relative_strength(seen, bench)
        # La force propre est calculee ici aussi : sans elle le backtest
        # mesurerait un systeme different de celui qui envoie les alertes.
        absolute = sg.absolute_strength(seen)
        score = sc.score(trend=trend, base=base, volume=volume, accum=accum,
                         comp=comp, rs=rs, absolute=absolute,
                         resistance=resistance, ext=ext, price=seen.close[-1],
                         pivot=ph.pivot_level(base, resistance))
        if score.total < min_score:
            continue
        plan = ph.risk_plan(seen, phase=phase, base=base, resistance=resistance,
                            min_rr=min_rr)
        if not plan.tradeable or not plan.targets:
            continue

        target = plan.targets[0].price
        outcome, exit_i, r = simulate(bars, i, plan.entry, plan.stop, target,
                                      max_bars=max_bars)
        if outcome == "NON_DECLENCHE":
            continue
        last_entry = i
        trades.append(Trade(ticker=ticker, phase=phase.name, entry_index=i,
                            entry_ts=bars.ts[i], exit_ts=bars.ts[exit_i],
                            entry=plan.entry, stop=plan.stop, target=target,
                            exit_index=exit_i, exit=bars.close[exit_i],
                            r_multiple=round(r, 4), bars_held=exit_i - i,
                            outcome=outcome,
                            period=_period_of(i, first_testable, last_testable)))
    return trades


def aggregate(trades: list[Trade], *, evaluated: int = 0) -> BacktestResult:
    # Les trades arrivent empiles valeur par valeur. Toute statistique de
    # SEQUENCE — au premier rang le drawdown — devient alors une fiction : elle
    # decrit une courbe de capital ou l'on aurait fini de trader Air Liquide
    # avant de commencer Bayer. On remet l'ordre du temps avant de compter.
    trades = sorted(trades, key=lambda t: (t.entry_ts, t.ticker))
    result = BacktestResult(trades=trades, evaluated=evaluated,
                            triggered=len(trades))
    result.overall = performance([t.r_multiple for t in trades])
    for name in {t.phase for t in trades}:
        result.by_phase[name] = performance(
            [t.r_multiple for t in trades if t.phase == name])
    for name in PERIODS:
        rows = [t.r_multiple for t in trades if t.period == name]
        if rows:
            result.by_period[name] = performance(rows)

    train = result.by_period.get(TRAIN)
    oos = result.by_period.get(OUT_OF_SAMPLE)
    if train and oos and train.trades >= 20 and oos.trades >= 20:
        drop = train.expectancy - oos.expectancy
        if drop > 0.3:
            result.notes.append(
                f"Attention : l'espérance chute de {drop:.2f}R entre apprentissage "
                "et hors échantillon — signe de sur-ajustement.")
        else:
            result.notes.append(
                "Espérance stable entre apprentissage et hors échantillon.")
    else:
        result.notes.append(
            "Découpage apprentissage/hors échantillon non concluant : trop peu de trades.")
    return result


def run(series: dict[str, tuple[md.Bars, md.Bars | None]], **kw) -> BacktestResult:
    trades: list[Trade] = []
    evaluated = 0
    for ticker, (bars, bench) in series.items():
        evaluated += max(0, len(bars))
        trades += walk(bars, bench, ticker=ticker, **kw)
    return aggregate(trades, evaluated=evaluated)
