from __future__ import annotations

"""Render the configuration sweep into the GitHub job summary."""

import json
import os
from pathlib import Path

REPORT = Path("backtest_sweep.json")


def main() -> int:
    lines = ["### Balayage — horizon du stop et fonction de tendance", ""]
    if not REPORT.exists():
        lines.append("Rapport absent.")
    else:
        d = json.loads(REPORT.read_text(encoding="utf-8"))
        lines += [f"{d['univers']} paires · {d['periode']} · {d['barres_evaluees']} barres",
                  f"Référence production : `{d['reference_production']}`", "",
                  "| Configuration | Entrées | Trades bruts | PF brut | Espérance brute "
                  "| Trades filtrés | PF filtré | Espérance filtrée |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- |"]
        for name, c in d["configurations"].items():
            a, b = c["sans_filtres"], c["avec_filtres"]
            lines.append(
                f"| {name} | {c['entrees_candidates']} | {a['trades']} | {a['profit_factor']} "
                f"| {a['expectancy_r']} | {b['trades']} | {b['profit_factor']} | {b['expectancy_r']} |"
            )
        lines += ["", "Aucun spread ni commission modélisé — le réel est moins bon."]

    text = "\n".join(lines) + "\n"
    print(text)
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
