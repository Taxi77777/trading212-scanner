from __future__ import annotations

"""Render the full-pipeline backtest report into the GitHub job summary."""

import json
import os
from pathlib import Path

REPORT = Path("backtest_results_v2.json")


def main() -> int:
    lines = ["### Backtest chaîne complète", ""]
    if not REPORT.exists():
        lines.append("Rapport absent — le backtest n'a pas produit de fichier.")
    else:
        d = json.loads(REPORT.read_text(encoding="utf-8"))
        lines.append(
            f"Univers : {d['universe']} paires · {d['period']} · "
            f"{d['bars_evaluated']} barres évaluées · {d['candidates']} entrées candidates"
        )
        lines += ["", "| Variante | Trades | Réussite | Profit factor | Espérance R | Total R | Max DD R |",
                  "| --- | --- | --- | --- | --- | --- | --- |"]
        for name, v in d["variants"].items():
            o = v["overall"]
            lines.append(
                f"| {name} | {o['trades']} | {o['win_rate']}% | {o['profit_factor']} | "
                f"{o['expectancy_r']} | {o['total_r']} | {o['max_dd_r']} |"
            )
        veto = d.get("veto_regime", {})
        lines += ["", f"Veto régime refuge : {veto.get('count', 0)} entrées écartées, "
                      f"{veto.get('r_evite', 0)} R évités.", ""]
        lines.append("Aucun spread ni commission modélisé — le réel est moins bon.")

    text = "\n".join(lines) + "\n"
    print(text)
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
