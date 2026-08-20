#!/usr/bin/env python3
"""Backtest du scanner sur données réelles.

    python run_backtest.py --markets US --limit 60 --step 5

Sans réseau, ce script ne peut rien produire : c'est voulu. Un backtest sur
données synthétiques ne prouve rien et ne doit pas pouvoir être confondu avec
une validation.
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor

from stockscan import backtest as bt
from stockscan import market_data as md
from stockscan import universe as uni


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Backtest du scanner pré-cassure")
    parser.add_argument("--markets", default="US")
    parser.add_argument("--limit", type=int, default=80)
    parser.add_argument("--step", type=int, default=5)
    parser.add_argument("--max-bars", type=int, default=60)
    parser.add_argument("--min-score", type=float, default=55.0)
    parser.add_argument("--min-rr", type=float, default=2.0)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--json", default="backtest_results.json")
    args = parser.parse_args(argv)

    codes = tuple(args.markets.split(",")) if args.markets else None
    stocks = uni.universe(codes)[:args.limit]
    data = md.MarketData()

    bench_symbols = {s.market.index_symbol for s in stocks}
    bench = {sym: data.daily(sym) for sym in bench_symbols}

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        bars = list(pool.map(lambda s: data.daily(s.symbol), stocks))

    series = {}
    for stock, rows in zip(stocks, bars):
        if rows is not None and len(rows) >= 400:
            series[stock.ticker] = (rows, bench.get(stock.market.index_symbol))

    print(f"{len(series)}/{len(stocks)} séries exploitables "
          f"({data.stats['calls']} requêtes)")
    if not series:
        print("Aucune donnée : backtest impossible. Rien n'est conclu.")
        return 1

    result = bt.run(series, step=args.step, max_bars=args.max_bars,
                    min_score=args.min_score, min_rr=args.min_rr)
    print()
    for line in result.report():
        print(line)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({
                "series": len(series), "trades": len(result.trades),
                "overall": vars(result.overall),
                "by_phase": {k: vars(v) for k, v in result.by_phase.items()},
                "by_period": {k: vars(v) for k, v in result.by_period.items()},
                "notes": result.notes,
                "sample": [vars(t) for t in result.trades[:200]],
            }, fh, ensure_ascii=False, indent=2, default=str)
        print(f"\nRésultats écrits dans {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
