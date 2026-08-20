from __future__ import annotations

"""Backtest H1 : deux ans d'historique et spread modélisé.

Pourquoi
--------
Le harnais M15 se heurte à un plafond de données : Yahoo ne sert que ~60 jours
de bougies 15 minutes, ce qui donne ~170 trades filtrés — trop peu pour
distinguer deux configurations (erreur-type 0,10 R). Les bougies horaires
remontent à **730 jours**, soit une douzaine de fois plus de calendrier et donc
bien plus de régimes de marché traversés.

Ce que ça change dans le moteur
-------------------------------
Le créneau ``m15`` du moteur est le *timeframe de déclenchement* : c'est lui qui
porte le trigger de cassure, l'ATR du stop, la liquidité et le régime de
volatilité. On y branche du **H1**. Le biais reste D1 + H4 + H1, mais le risque
vit désormais sur la même échelle que la thèse — au lieu d'un stop de quelques
minutes sur une idée de plusieurs jours.

Le spread
---------
Aucun chiffre antérieur ne le comptait. Sur un stop de 15-20 pips, 1,5 pip
représente 8 à 10 % du risque à chaque trade. Il est retiré du résultat en
unités de R (``r_net = r_brut - spread / risque``) et le rapport donne les deux
versions, pour que son poids soit visible plutôt que supposé.

Budget de calcul
----------------
Avec un déclenchement horaire, l'historique complet est recoupé à *chaque*
bougie, là où le harnais M15 ne le refaisait qu'à chaque heure. Deux mesures
gardent le run dans le budget CI sans toucher à la fidélité :

* les coupes sont bornées (``D1_TAIL`` / ``H1_TAIL``) — les EMA 200 sont
  largement convergées bien avant ces longueurs ;
* le H4 est mémorisé par godet de 4 heures, ce qui est de toute façon la
  discipline de bougie clôturée.

L'univers est réduit aux paires les plus liquides : ce sont aussi celles dont le
spread est le plus faible, et le balayage M15 a montré que les croisements
larges étaient les pires. Le choix se défend seul, il n'est pas qu'un compromis
de temps de calcul.
"""

import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import forex_backtest_v2 as bt
import forex_intraday_scanner_v3 as scanner
import forex_quality
import run_forex_v7 as v7

OUT = Path("backtest_results_h1.json")

H1_RANGE = "730d"
D1_RANGE = "2y"
ENTRY_BAR_SECONDS = 3600
ENTRY_TAIL = 400
D1_TAIL = 400
H1_TAIL = 900          # -> ~225 bougies H4, au-dessus des 205 exigées par trend()
MAX_HOLD_BARS = 120    # 5 jours en H1, l'échelle de la thèse D1/H4

UNIVERSE = (
    "EURUSD=X", "GBPUSD=X", "USDJPY=X", "USDCHF=X", "AUDUSD=X", "NZDUSD=X",
    "USDCAD=X", "EURGBP=X", "EURJPY=X", "GBPJPY=X", "AUDJPY=X", "EURCHF=X",
)

# Spreads retail indicatifs, en pips. Volontairement conservateurs et arrondis :
# ce sont des ordres de grandeur, pas des cotations. Le rapport donne aussi le
# résultat sans spread — l'écart entre les deux est la seule chose à lire ici.
SPREAD_PIPS = {
    "EURUSD=X": 1.0, "GBPUSD=X": 1.2, "USDJPY=X": 1.0, "USDCHF=X": 1.5,
    "AUDUSD=X": 1.2, "NZDUSD=X": 1.6, "USDCAD=X": 1.5, "EURGBP=X": 1.5,
    "EURJPY=X": 1.5, "GBPJPY=X": 2.5, "AUDJPY=X": 1.8, "CADJPY=X": 2.0,
    "EURCHF=X": 1.8, "EURAUD=X": 2.0, "EURNZD=X": 3.0, "GBPAUD=X": 3.0,
    "GBPCAD=X": 3.0, "GBPCHF=X": 3.0, "GBPNZD=X": 4.0, "AUDCAD=X": 2.0,
    "AUDCHF=X": 2.5, "CADCHF=X": 2.5, "NZDCAD=X": 2.5, "NZDCHF=X": 3.0,
}
DEFAULT_SPREAD_PIPS = 2.5


def pip_size(symbol: str) -> float:
    return 0.01 if "JPY" in symbol.upper() else 0.0001


def spread_price(symbol: str) -> float:
    return SPREAD_PIPS.get(symbol, DEFAULT_SPREAD_PIPS) * pip_size(symbol)


class H1Series(bt.Series):
    """Series whose trigger slot is H1, with cuts bounded and H4 memoised.

    ``bt.Series`` rebuilds the full history on every miss, which is fine when
    the trigger is M15 (the H1 count only changes once an hour) and ruinous when
    the trigger *is* H1 — the cache would miss on every single bar.
    """

    def __init__(self, symbol, d1, h1):
        super().__init__(symbol, d1, h1, h1)
        self._h4_cache: dict[int, object] = {}

    def completed(self, close_time: int):
        nd = self.d1_count(close_time)
        nh = self.h1_count(close_time)
        d1c = bt.tail(self.d1, nd, D1_TAIL)
        h1c = bt.tail(self.h1, nh, H1_TAIL)
        bucket = nh // 4
        h4c = self._h4_cache.get(bucket)
        if h4c is None:
            self._h4_cache.clear()
            h4c = scanner.resample_h4(h1c) if h1c else None
            self._h4_cache[bucket] = h4c
        return d1c, h4c, h1c


# --------------------------------------------------------------------------- #
def collect(series: dict[str, H1Series], context: bt.Context):
    """Every H1 ENTREE the engine would have produced, with its verdicts."""
    candidates: list[dict] = []
    evaluated = 0
    for symbol, s in series.items():
        entry_bars = s.m15          # the trigger slot holds H1 bars here
        if not entry_bars:
            continue
        for i in range(250, len(entry_bars.ts) - 1):
            close_time = entry_bars.ts[i] + ENTRY_BAR_SECONDS
            d1c, h4c, h1c = s.completed(close_time)
            if not d1c or not h1c or not h4c:
                continue
            if len(d1c.close) < 205 or len(h1c.close) < 205 or len(h4c.close) < 60:
                continue
            entry_cut = bt.tail(entry_bars, i + 1, ENTRY_TAIL)
            if not entry_cut or len(entry_cut.close) < 60:
                continue

            strength, intraday, (regime_label, regime_score) = context.at(close_time)
            scanner.INTRADAY_STRENGTH.clear()
            scanner.INTRADAY_STRENGTH.update(intraday)
            scanner.INTRADAY_REGIME.clear()
            scanner.INTRADAY_REGIME.update({"label": regime_label, "score": regime_score})

            frames = {"d1": d1c, "h4": h4c, "h1": h1c, "m15": entry_cut}
            old_session = scanner.session_name
            scanner.session_name = lambda ts=entry_bars.ts[i]: bt.session_for(ts)
            try:
                sig = scanner.build_signal(symbol, frames, strength,
                                           "MIXTE", "", "BACKTEST_NO_NEWS", False)
            except Exception:
                sig = None
            finally:
                scanner.session_name = old_session
            evaluated += 1
            if sig is None or sig.state != "ENTREE":
                continue

            coherence = forex_quality.coherence(sig)
            candidates.append({
                "symbol": symbol, "pair": sig.pair, "side": sig.side,
                "score": sig.score, "ts": entry_bars.ts[i], "idx": i,
                "entry": entry_bars.open[i + 1], "sl": sig.sl, "tp1": sig.tp1,
                "coherence": coherence["verdict"], "veto": coherence.get("veto"),
                "regime": regime_label, "sig": sig,
            })
    return candidates, evaluated


def resolve(bars, start, side, entry, sl, tp1, cost_price: float):
    """Forward-scan on H1 bars; the spread is charged in R on the outcome."""
    risk = abs(entry - sl)
    if risk <= 0:
        return 0.0, "NO_RISK", bars.ts[start]
    cost_r = cost_price / risk
    last = min(len(bars.close) - 1, start + MAX_HOLD_BARS)
    for i in range(start, last + 1):
        hi, lo = bars.high[i], bars.low[i]
        sl_hit = lo <= sl if side == "BUY" else hi >= sl
        tp_hit = hi >= tp1 if side == "BUY" else lo <= tp1
        if sl_hit and tp_hit:
            return -1.0 - cost_r, "AMBIGUOUS_SAME_BAR", bars.ts[i]
        if sl_hit:
            return -1.0 - cost_r, "SL", bars.ts[i]
        if tp_hit:
            return abs(tp1 - entry) / risk - cost_r, "TP1", bars.ts[i]
    close = bars.close[last]
    gross = (close - entry) / risk if side == "BUY" else (entry - close) / risk
    return gross - cost_r, "TIMEOUT", bars.ts[last]


def heatmap(trades: list[dict]) -> dict:
    """Paire x mois, en R.

    Une espérance négative globale peut recouvrir deux réalités très
    différentes : une perte régulière partout, ou quelques mois et quelques
    paires qui saignent pendant que le reste tient. Seul le second cas laisse
    une piste. La grille répond à cette question — elle ne prédit rien.
    """
    cells: dict[str, dict[str, list[float]]] = {}
    months: set[str] = set()
    for t in trades:
        month = datetime.fromtimestamp(t["ts"], tz=timezone.utc).strftime("%Y-%m")
        months.add(month)
        cells.setdefault(t["pair"], {}).setdefault(month, []).append(t["r"])
    grid = {
        pair: {m: {"r": round(sum(v), 2), "n": len(v)} for m, v in sorted(by_month.items())}
        for pair, by_month in sorted(cells.items())
    }
    par_mois = {}
    for m in sorted(months):
        rs = [r for by_month in cells.values() for r in by_month.get(m, [])]
        par_mois[m] = {"r": round(sum(rs), 2), "n": len(rs)}
    return {"mois": sorted(months), "grille": grid, "par_mois": par_mois}


def run(candidates, series, use_filters: bool, spread_factor: float):
    dropped = {"coherence": 0, "veto": 0, "exposure": 0, "cooldown": 0, "quota": 0}
    open_trades: list[dict] = []
    trades: list[dict] = []
    last_sent: dict[tuple[str, str], int] = {}

    slots: dict[int, list[dict]] = {}
    for c in candidates:
        slots.setdefault(c["ts"] - (c["ts"] % ENTRY_BAR_SECONDS), []).append(c)

    for slot in sorted(slots):
        sent = 0
        for c in sorted(slots[slot], key=lambda x: x["score"], reverse=True):
            if use_filters and c["coherence"] == forex_quality.INCOHERENT:
                dropped["veto" if c["veto"] else "coherence"] += 1
                continue
            if sent >= scanner.MAX_ALERTS:
                dropped["quota"] += 1
                continue
            key = (c["pair"], c["side"])
            if c["ts"] - last_sent.get(key, -10**9) < scanner.COOLDOWN * 60:
                dropped["cooldown"] += 1
                continue

            open_trades = [t for t in open_trades if t["exit_ts"] > c["ts"]]
            legs = v7._exposure(c["sig"])
            if use_filters and v7.MAX_PER_CURRENCY > 0:
                held: dict = {}
                for t in open_trades:
                    for leg in t["legs"]:
                        held[leg] = held.get(leg, 0) + 1
                if any(held.get(leg, 0) >= v7.MAX_PER_CURRENCY for leg in legs):
                    dropped["exposure"] += 1
                    continue

            bars = series[c["symbol"]].m15
            cost = spread_price(c["symbol"]) * spread_factor
            r, reason, exit_ts = resolve(bars, c["idx"] + 1, c["side"],
                                         c["entry"], c["sl"], c["tp1"], cost)
            trade = {"pair": c["pair"], "side": c["side"], "score": c["score"],
                     "r": r, "exit": reason, "ts": c["ts"], "exit_ts": exit_ts,
                     "legs": legs}
            open_trades.append(trade)
            trades.append(trade)
            sent += 1
            last_sent[key] = c["ts"]
    return trades, dropped


def main() -> int:
    started = time.time()

    series: dict[str, H1Series] = {}
    for symbol in UNIVERSE:
        try:
            d1 = bt.fetch(symbol, "1d", D1_RANGE)
            h1 = bt.fetch(symbol, "1h", H1_RANGE)
        except Exception as exc:
            print(f"{symbol}: donnees indisponibles ({exc})")
            continue
        if d1 and h1 and len(h1.close) > 600:
            series[symbol] = H1Series(symbol, d1, h1)
    if not series:
        print("Aucune paire exploitable.")
        return 1
    moyenne = statistics.fmean(len(s.m15.close) for s in series.values())
    print(f"{len(series)}/{len(UNIVERSE)} paires chargees "
          f"({moyenne:.0f} bougies H1 en moyenne)", flush=True)

    context = bt.Context(series)
    candidates, evaluated = collect(series, context)
    print(f"{evaluated} bougies evaluees, {len(candidates)} entrees candidates "
          f"({time.time() - started:.0f}s)", flush=True)

    variants = {}
    for label, (filters, spread) in {
        "1_sans_filtres_sans_spread": (False, 0.0),
        "2_sans_filtres_avec_spread": (False, 1.0),
        "3_avec_filtres_sans_spread": (True, 0.0),
        "4_avec_filtres_avec_spread": (True, 1.0),
    }.items():
        trades, dropped = run(candidates, series, filters, spread)
        by_pair: dict[str, list[dict]] = {}
        for t in trades:
            by_pair.setdefault(t["pair"], []).append(t)
        st = bt.stats(trades)
        se = (statistics.stdev(t["r"] for t in trades) / len(trades) ** 0.5) if len(trades) > 1 else None
        variants[label] = {
            "overall": st,
            "erreur_type": round(se, 4) if se else None,
            "ic95": [round(st["expectancy_r"] - 1.96 * se, 4),
                     round(st["expectancy_r"] + 1.96 * se, 4)] if se else None,
            "rejets": dropped,
            "by_pair": {k: bt.stats(v) for k, v in sorted(by_pair.items())},
            "heatmap": heatmap(trades),
        }
        print(f"{label:30} n={st['trades']:5} PF {st['profit_factor']} "
              f"exp {st['expectancy_r']:+.4f} R IC95 {variants[label]['ic95']}", flush=True)

    report = {
        "moteur": "Forex v7 - declenchement H1 au lieu de M15",
        "univers": sorted(series),
        "periode": f"{D1_RANGE} D1 / {H1_RANGE} H1 (declenchement H1)",
        "bougies_evaluees": evaluated,
        "entrees_candidates": len(candidates),
        "seuils": {"SETUP_MIN": scanner.SETUP_MIN, "FINAL_MIN": scanner.FINAL_MIN,
                   "COOLDOWN_MIN": scanner.COOLDOWN, "MAX_ALERTS": scanner.MAX_ALERTS,
                   "MAX_PER_CCY": v7.MAX_PER_CURRENCY,
                   "MAX_HOLD_BARS_H1": MAX_HOLD_BARS},
        "variantes": variants,
        "notes": [
            "Le creneau de declenchement contient du H1 : trigger, ATR du stop, "
            "liquidite et volatilite sont donc horaires.",
            "Force intraday mesuree sur 16 et 96 bougies H1, soit ~16 h et ~4 jours.",
            "Spreads retail indicatifs et arrondis, retires en R sur le resultat.",
            "H4 memorise par godet de 4 h : discipline de bougie cloturee.",
            "Taux directeurs et news historiques exclus (sources point-in-time).",
            f"Sortie forcee apres {MAX_HOLD_BARS} bougies H1 si ni SL ni TP touche.",
        ],
        "reference_m15": {
            "avec_filtres": {"trades": 167, "profit_factor": 0.685, "expectancy_r": -0.197},
            "commentaire": "60 jours M15, 24 paires, sans spread - erreur-type 0,10 R",
        },
        "runtime_s": round(time.time() - started, 1),
    }
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: {"overall": v["overall"], "ic95": v["ic95"]}
                      for k, v in variants.items()}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
