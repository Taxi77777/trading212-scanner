from __future__ import annotations

"""Rend le rapport H1 dans le résumé de job GitHub, heatmap comprise."""

import json
import os
from pathlib import Path

REPORT = Path("backtest_results_h1.json")
VARIANT = "4_avec_filtres_avec_spread"   # la seule qui reflète la réalité


def cell(value: float) -> str:
    """Nuance textuelle : le résumé GitHub ne rend ni couleur ni HTML riche."""
    if value >= 5:
        return f"🟩 {value:+.1f}"
    if value > 0:
        return f"🟢 {value:+.1f}"
    if value > -5:
        return f"🟠 {value:+.1f}"
    return f"🟥 {value:+.1f}"


def main() -> int:
    lines = ["### Backtest H1 — 2 ans, spread modélisé", ""]
    if not REPORT.exists():
        lines.append("Rapport absent.")
    else:
        d = json.loads(REPORT.read_text(encoding="utf-8"))
        lines += [
            f"{len(d['univers'])} paires · {d['periode']} · "
            f"{d['bougies_evaluees']} bougies · {d['entrees_candidates']} entrées candidates",
            "",
            "| Variante | Trades | Réussite | Profit factor | Espérance R | IC 95 % | Total R |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for name, v in d["variantes"].items():
            o = v["overall"]
            ic = v.get("ic95")
            ic_txt = f"[{ic[0]:+.3f} ; {ic[1]:+.3f}]" if ic else "n/a"
            lines.append(
                f"| {name} | {o['trades']} | {o['win_rate']}% | {o['profit_factor']} "
                f"| {o['expectancy_r']:+.4f} | {ic_txt} | {o['total_r']} |"
            )

        ref = d.get("reference_m15", {}).get("avec_filtres", {})
        if ref:
            lines += ["", f"*Référence M15 (60 jours, sans spread) : {ref['trades']} trades, "
                          f"PF {ref['profit_factor']}, espérance {ref['expectancy_r']:+.3f} R.*"]

        hm = d["variantes"].get(VARIANT, {}).get("heatmap")
        if hm and hm["mois"]:
            months = hm["mois"]
            lines += ["", f"#### Heatmap paire × mois — `{VARIANT}` (R net)", "",
                      "| Paire | " + " | ".join(m for m in months) + " |",
                      "| --- |" + " --- |" * len(months)]
            for pair, row in hm["grille"].items():
                cells = [cell(row[m]["r"]) if m in row else "·" for m in months]
                lines.append(f"| **{pair}** | " + " | ".join(cells) + " |")
            totals = [cell(hm["par_mois"][m]["r"]) for m in months]
            lines.append("| **TOTAL** | " + " | ".join(totals) + " |")
            lines += ["", "🟩 ≥ +5 R · 🟢 > 0 · 🟠 0 à −5 R · 🟥 < −5 R · `·` aucun trade"]

        lines += ["", "Spreads retail indicatifs. Taux directeurs et news historiques exclus."]

    text = "\n".join(lines) + "\n"
    print(text)
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
