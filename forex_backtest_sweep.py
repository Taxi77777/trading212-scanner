from __future__ import annotations

"""Two questions the v2 report raised, answered on identical bars.

1. **Horizon du stop.** The bias comes from D1/H4 but the stop is sized on the
   M15 ATR — a thesis that plays out over days, risked over minutes. Compare
   ``FOREX_STOP_ATR_TF=m15`` (shipped) against ``h1``.
2. **Tendance stricte ou flexible.** ``run_forex_v5`` replaced the v3 ``trend``
   with a far more permissive one; on the same 60 days the production engine
   produced 4331 entries where the v3 engine produced 294. Compare both.

Four configurations, each reported with and without the coherence/veto/exposure
layer. Data is downloaded once and every configuration replays the exact same
bars, so the only thing that differs is the knob under test.
"""

import json
import os
import time
from pathlib import Path

import forex_backtest_v2 as bt
import forex_intraday_scanner_v3 as scanner
import run_forex_v5 as v5

OUT = Path("backtest_sweep.json")

TRENDS = {"v3_strict": v5.strict_trend, "v5_flexible": v5.flexible_trend}
STOPS = ("m15", "h1")


def main() -> int:
    started = time.time()

    series: dict[str, bt.Series] = {}
    for symbol in bt.PAIRS:
        try:
            d1 = bt.fetch(symbol, "1d", "2y")
            h1 = bt.fetch(symbol, "1h", "60d")
            m15 = bt.fetch(symbol, "15m", "60d")
        except Exception as exc:
            print(f"{symbol}: données indisponibles ({exc})")
            continue
        if d1 and h1 and m15:
            series[symbol] = bt.Series(symbol, d1, h1, m15)
    print(f"{len(series)}/{len(bt.PAIRS)} paires chargées", flush=True)

    # Strength and the completed-bar cuts do not depend on the knobs, so the
    # context is built once and shared by all four configurations.
    context = bt.Context(series)

    results = {}
    saved_trend = scanner.trend
    saved_stop = scanner.STOP_ATR_TF
    try:
        for trend_name, trend_fn in TRENDS.items():
            for stop in STOPS:
                label = f"{trend_name}__stop_{stop}"
                scanner.trend = trend_fn
                scanner.STOP_ATR_TF = stop
                t0 = time.time()
                candidates, evaluated = bt.collect_candidates(series, context)
                brut, _ = bt.run_variant(candidates, series, False, False, True)
                filtre, dropped = bt.run_variant(candidates, series, True, True, True)
                results[label] = {
                    "entrees_candidates": len(candidates),
                    "sans_filtres": bt.stats(brut),
                    "avec_filtres": bt.stats(filtre),
                    "rejets": dropped,
                    "secondes": round(time.time() - t0, 1),
                }
                o1, o2 = results[label]["sans_filtres"], results[label]["avec_filtres"]
                print(f"{label:28} cand={len(candidates):5} "
                      f"| brut PF {o1['profit_factor']} exp {o1['expectancy_r']} n={o1['trades']} "
                      f"| filtré PF {o2['profit_factor']} exp {o2['expectancy_r']} n={o2['trades']}",
                      flush=True)
    finally:
        scanner.trend = saved_trend
        scanner.STOP_ATR_TF = saved_stop

    report = {
        "question_1": "Horizon du stop : ATR M15 (livré) vs ATR H1",
        "question_2": "Tendance : v3 stricte vs v5 flexible (celle en production)",
        "univers": len(series),
        "periode": "60d M15 / 60d H1 / 2y D1",
        "barres_evaluees": evaluated,
        "reference_production": "v5_flexible__stop_m15",
        "configurations": results,
        "notes": [
            "Cooldown 240 min et quota 3 alertes/scan appliqués partout.",
            "Aucun spread ni commission modélisé — le réel est moins bon.",
            "Horizon de sortie fixe à 96 barres M15 (24 h) dans tous les cas :",
            "un stop plus large sans allonger la durée produit plus de TIMEOUT.",
        ],
        "runtime_s": round(time.time() - started, 1),
    }
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(results, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
