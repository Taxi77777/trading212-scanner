#!/usr/bin/env python3
"""Point d'entrée du scanner pré-cassure.

    python run_scan.py preflight          vérifie données, IA, Telegram, EDGAR
    python run_scan.py scan --dry-run     scanne sans rien envoyer
    python run_scan.py scan               scanne et envoie l'alerte Telegram

Aucun secret n'est affiché : le préflight ne dit que « présent » ou « absent ».
"""
from __future__ import annotations

import argparse
import json
import sys

from stockscan import ai_judge, market_data as md, telegram, universe as uni
from stockscan import fundamentals as fu
from stockscan.scan import Config, run

OK, KO, WARN = "OK", "ÉCHEC", "ABSENT"


def _line(label: str, state: str, detail: str = "") -> str:
    return f"[{state:^6}] {label}" + (f" — {detail}" if detail else "")


def preflight(args) -> int:
    print("=== PRÉFLIGHT SCANNER PRÉ-CASSURE ===")
    failures = 0

    stocks = uni.universe()
    counts = {code: len(rows) for code, rows in uni.by_market().items()}
    print(_line("Univers", OK if stocks else KO,
                f"{len(stocks)} valeurs — " + ", ".join(f"{k} {v}" for k, v in counts.items())))
    if not stocks:
        failures += 1

    data = md.MarketData()
    probe = data.daily("^GSPC")
    if probe and len(probe) > 200:
        print(_line("Données Yahoo", OK, f"^GSPC : {len(probe)} séances, "
                                         f"dernier cours {probe.close[-1]:.2f}"))
    else:
        print(_line("Données Yahoo", KO, "aucune série exploitable"))
        failures += 1

    sample = data.daily(stocks[0].symbol) if stocks else None
    if sample and len(sample) > 200:
        print(_line("Données actions", OK, f"{stocks[0].ticker} : {len(sample)} séances"))
    else:
        print(_line("Données actions", KO, "échantillon indisponible"))
        failures += 1

    if ai_judge.configured():
        report = ai_judge.check_connectivity()
        state = OK if report["connected"] else KO
        detail = (f"modèle {report['model']}, HTTP {report['http']}, "
                  f"verdict {report.get('verdict', '—')}")
        if not report["connected"]:
            detail += f" — {report['error']}"
            failures += 1
        print(_line("Cloudflare Workers AI", state, detail))
    else:
        print(_line("Cloudflare Workers AI", WARN, "secrets non fournis, IA désactivée"))

    print(_line("Telegram", OK if telegram.configured() else WARN,
                "jeton et discussion présents" if telegram.configured()
                else "secrets non fournis, envoi désactivé"))
    if not telegram.configured():
        failures += 1

    table = fu.SecClient().tickers()
    print(_line("SEC EDGAR", OK if table else WARN,
                f"{len(table)} tickers américains" if table
                else "injoignable, fondamentaux ignorés"))

    print(f"\n{'PRÉFLIGHT OK' if not failures else f'{failures} vérification(s) en échec'}")
    return 1 if failures else 0


def scan(args) -> int:
    cfg = Config(
        markets=tuple(args.markets.split(",")) if args.markets else None,
        limit=args.limit, top=args.top, min_score=args.min_score,
        min_prebreakout=args.min_prebreakout, min_rr=args.min_rr,
        use_ai=not args.no_ai, use_fundamentals=not args.no_fundamentals,
    )
    summary, candidates = run(cfg)

    print(f"{summary.analysed} valeurs analysées, {summary.failed} indisponibles, "
          f"{summary.kept} retenues, {summary.duration_s}s")
    for name, count in sorted(summary.counts.items()):
        print(f"  {name:<14} {count}")
    for c in candidates:
        print(f"  {c.phase.emoji} {c.ticker:<8} {c.phase.name:<13} "
              f"score {c.score.total:5.1f} pré-cassure {c.score.prebreakout:5.1f} "
              f"R:R {c.plan.rr:.2f}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({
                "date": summary.date, "analysed": summary.analysed,
                "failed": summary.failed, "kept": summary.kept,
                "counts": summary.counts, "duration_s": summary.duration_s,
                "regime": None if not summary.regime else {
                    "label": summary.regime.label, "score": summary.regime.score,
                    "breadth_pct": summary.regime.breadth_pct},
                "candidates": [{
                    "ticker": c.ticker, "market": c.market, "phase": c.phase.name,
                    "score": c.score.total, "prebreakout": c.score.prebreakout,
                    "grade": c.score.grade, "entry": c.plan.entry, "stop": c.plan.stop,
                    "rr": c.plan.rr, "ai": c.ai.get("verdict", ""),
                } for c in candidates],
            }, fh, ensure_ascii=False, indent=2)
        print(f"Rapport écrit dans {args.json}")

    if args.dry_run:
        for message in telegram.build_report(summary, candidates):
            print("\n--- message Telegram ---")
            print(message)
        return 0

    result = telegram.send_report(summary, candidates)
    print(f"Telegram : {result['sent']}/{result['total']} message(s) envoyé(s)")
    for error in result["errors"]:
        print(f"  erreur : {error}")
    return 0 if result["ok"] else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Scanner actions pré-cassure")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("preflight", help="vérifier données, IA, Telegram, EDGAR")

    run_parser = sub.add_parser("scan", help="lancer un scan")
    run_parser.add_argument("--markets", default="", help="ex. US,FR,DE")
    run_parser.add_argument("--limit", type=int, default=None)
    run_parser.add_argument("--top", type=int, default=5)
    run_parser.add_argument("--min-score", type=float, default=55.0)
    run_parser.add_argument("--min-prebreakout", type=float, default=45.0)
    run_parser.add_argument("--min-rr", type=float, default=2.0)
    run_parser.add_argument("--no-ai", action="store_true")
    run_parser.add_argument("--no-fundamentals", action="store_true")
    run_parser.add_argument("--dry-run", action="store_true",
                            help="afficher les messages sans les envoyer")
    run_parser.add_argument("--json", default="", help="fichier de rapport JSON")

    args = parser.parse_args(argv)
    if args.command == "preflight":
        return preflight(args)
    if args.command == "scan":
        return scan(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
