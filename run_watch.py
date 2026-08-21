#!/usr/bin/env python3
"""Surveillance intraséance des plans en cours.

    python run_watch.py               vérifie et alerte si quelque chose bouge
    python run_watch.py --dry-run     affiche sans envoyer

Silencieux par construction : sans franchissement, aucun message. Une alerte
qui se répète toutes les trente minutes cesse d'être lue.
"""
from __future__ import annotations

import argparse
import sys
import time

from stockscan import market_data as md
from stockscan import telegram
from stockscan import watchlist as wl

FICHIER = "watchlist.json"
INTRADAY = ("15m", "5d")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Surveillance des plans en cours")
    parser.add_argument("--file", default=FICHIER)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    items = wl.load(args.file)
    vivants = [w for w in items if w.alive]
    if not vivants:
        print("Aucun plan à surveiller.")
        return 0

    data = md.MarketData()
    evenements = []
    for item in vivants:
        bars = data.fetch(item.symbol, *INTRADAY)
        event = wl.inspect(item, bars)
        etat = "déclenché" if item.triggered else "en attente"
        if item.closed:
            etat = item.outcome.lower()
        print(f"  {item.ticker:8} {etat:12} "
              f"{'ALERTE ' + event.kind if event else ''}")
        if event:
            evenements.append(event)

    wl.save(args.file, items)
    print(f"{len(vivants)} plan(s) surveillé(s), {len(evenements)} alerte(s), "
          f"{data.stats['calls']} requêtes")

    if not evenements:
        return 0
    if args.dry_run:
        for event in evenements:
            print("\n--- message Telegram ---")
            print(telegram.format_event(event))
        return 0

    resultat = telegram.send_events(evenements)
    print(f"Telegram : {resultat['sent']}/{resultat['total']} envoyé(s)")
    for erreur in resultat["errors"]:
        print(f"  erreur : {erreur}")
    return 0 if resultat["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
