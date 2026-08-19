from __future__ import annotations

"""Walk-forward backtest of the *full* Forex v7 decision chain.

``forex_backtest_v1`` calls ``scanner.build_signal`` directly and therefore
measures the quantitative engine alone. Everything added since — the
multi-factor coherence gate, the intraday safe-haven regime veto and the
currency-exposure cap — sits downstream of it and was invisible to that
harness. This version replays the same history through each layer and reports
the three variants side by side, so the effect of each filter is a number
rather than an argument.

Method
------
* Completed-bar discipline: D1 and H1 bars are only visible once closed;
  higher-timeframe context is cut to the evaluation instant.
* Entry at the next M15 open, SL/TP resolved bar by bar, same-bar SL+TP
  conservatively booked as -1R.
* Signals from all pairs are merged chronologically, so the exposure cap sees
  the portfolio as it actually was.
* No spread or commission is modelled — real results are worse than these.
* Historical central-bank rates and news are excluded: the live sources are
  point-in-time, and applying today's values backwards would be look-ahead bias.
"""

import json
import math
import statistics
import time
from bisect import bisect_right
from datetime import datetime, timezone
from pathlib import Path

import requests

import forex_intraday_scanner_v3 as scanner
import forex_quality
import run_forex_v7 as v7

OUT = Path("backtest_results_v2.json")
YAHOO = scanner.YAHOO

PAIRS = dict(v7.scanner.PAIRS)

# M15 tail kept for each evaluation. Every M15 consumer in the engine reads at
# most the last ~45 bars (ATR 14, EMA 20, 12/20-bar ranges, and the 30 rolling
# ATR windows of volatility_regime), so a 400-bar tail is faithful and keeps the
# run inside the CI budget.
M15_TAIL = 400
MAX_HOLD_BARS = 96          # 24 h on M15, same as v1
RR_TP1 = 1.7

session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0 T212ForexBacktest/2.0"})


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #
def fetch(symbol: str, interval: str, range_: str) -> scanner.Bars | None:
    r = session.get(f"{YAHOO}/{symbol}",
                    params={"range": range_, "interval": interval,
                            "includePrePost": "false", "events": "div,splits"},
                    timeout=25)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    ts = res.get("timestamp", [])
    q = res["indicators"]["quote"][0]
    rows = []
    for i, t in enumerate(ts):
        vals = (q["open"][i], q["high"][i], q["low"][i], q["close"][i], q["volume"][i])
        if all(v is not None for v in vals):
            rows.append((int(t), *map(float, vals)))
    if len(rows) < 60:
        return None
    return scanner.Bars([x[0] for x in rows], [x[1] for x in rows], [x[2] for x in rows],
                        [x[3] for x in rows], [x[4] for x in rows], [x[5] for x in rows])


def tail(d: scanner.Bars | None, n: int, length: int) -> scanner.Bars | None:
    """Bars[:n] truncated to its last *length* entries."""
    if not d or n <= 0:
        return None
    lo = max(0, n - length)
    return scanner.Bars(d.ts[lo:n], d.open[lo:n], d.high[lo:n],
                        d.low[lo:n], d.close[lo:n], d.volume[lo:n])


class Series:
    """Pre-indexed history for one symbol, with memoised completed-bar cuts."""

    def __init__(self, symbol: str, d1, h1, m15):
        self.symbol = symbol
        self.d1, self.h1, self.m15 = d1, h1, m15
        self._d1_close = [t + 86400 for t in (d1.ts if d1 else [])]
        self._h1_close = [t + 3600 for t in (h1.ts if h1 else [])]
        self._cache: dict = {}

    def d1_count(self, close_time: int) -> int:
        return bisect_right(self._d1_close, close_time)

    def h1_count(self, close_time: int) -> int:
        return bisect_right(self._h1_close, close_time)

    def completed(self, close_time: int):
        """Return (d1_cut, h4_cut, h1_cut) — rebuilt only when a bar closes."""
        nd, nh = self.d1_count(close_time), self.h1_count(close_time)
        key = (nd, nh)
        hit = self._cache.get(key)
        if hit is None:
            self._cache.clear()   # only the newest cut is ever needed
            d1c = tail(self.d1, nd, 100000)
            h1c = tail(self.h1, nh, 100000)
            h4c = scanner.resample_h4(h1c) if h1c else None
            hit = (d1c, h4c, h1c)
            self._cache[key] = hit
        return hit


# --------------------------------------------------------------------------- #
# Strength / regime, computed once per 15-minute slot for the whole universe
# --------------------------------------------------------------------------- #
def _ret_at(closes: list[float], i: int, n: int) -> float | None:
    """Faithful to scanner.ret on a series cut at index i (inclusive)."""
    if i < 0 or i - n < 0:
        return None
    return ((closes[i] / closes[i - n]) - 1) * 100


class Context:
    def __init__(self, series: dict[str, Series]):
        self.series = series
        # Strength is a property of the whole universe, not of one pair, so it
        # is computed once per 15-minute slot and shared by all 24 symbols.
        # A single-entry cache would never hit here: the loop is symbol-major.
        self._cache: dict[int, tuple] = {}

    def at(self, close_time: int):
        slot = close_time - (close_time % 900)
        hit = self._cache.get(slot)
        if hit is not None:
            return hit

        daily: dict[str, list[float]] = {}
        intra: dict[str, list[float]] = {}
        for symbol, s in self.series.items():
            base, quote, _ = PAIRS[symbol]
            nd = s.d1_count(close_time)
            if s.d1 and nd:
                vals = [v for v in (_ret_at(s.d1.close, nd - 1, n) for n in (5, 20, 60))
                        if v is not None]
                if vals:
                    r = statistics.fmean(vals)
                    daily.setdefault(base, []).append(r)
                    daily.setdefault(quote, []).append(-r)
            if s.m15:
                i = bisect_right(s.m15.ts, close_time) - 1
                vals = [v for v in (_ret_at(s.m15.close, i, n) for n in (16, 96))
                        if v is not None]
                if vals:
                    r = statistics.fmean(vals)
                    intra.setdefault(base, []).append(r)
                    intra.setdefault(quote, []).append(-r)

        strength = {k: statistics.fmean(v) for k, v in daily.items() if v}
        intraday = {k: statistics.fmean(v) for k, v in intra.items() if v}
        regime = scanner.intraday_risk_regime(intraday)
        value = (strength, intraday, regime)
        self._cache[slot] = value
        return value


# --------------------------------------------------------------------------- #
# Trade resolution
# --------------------------------------------------------------------------- #
def session_for(ts: int) -> str:
    h = datetime.fromtimestamp(ts, tz=timezone.utc)
    hour = h.hour + h.minute / 60
    if 0 <= hour < 7:
        return "ASIE"
    if 7 <= hour < 12:
        return "LONDRES"
    if 12 <= hour < 17:
        return "LONDRES + NEW YORK"
    if 17 <= hour < 21:
        return "NEW YORK"
    return "ASIE"


def resolve(m15: scanner.Bars, start: int, side: str, entry: float, sl: float, tp1: float):
    """Return (r_multiple, reason, exit_timestamp)."""
    risk = abs(entry - sl)
    if risk <= 0:
        return 0.0, "NO_RISK", m15.ts[start]
    last = min(len(m15.close) - 1, start + MAX_HOLD_BARS)
    for i in range(start, last + 1):
        hi, lo = m15.high[i], m15.low[i]
        sl_hit = lo <= sl if side == "BUY" else hi >= sl
        tp_hit = hi >= tp1 if side == "BUY" else lo <= tp1
        if sl_hit and tp_hit:
            return -1.0, "AMBIGUOUS_SAME_BAR", m15.ts[i]
        if sl_hit:
            return -1.0, "SL", m15.ts[i]
        if tp_hit:
            return abs(tp1 - entry) / risk, "TP1", m15.ts[i]
    close = m15.close[last]
    r = (close - entry) / risk if side == "BUY" else (entry - close) / risk
    return float(r), "TIMEOUT", m15.ts[last]


def stats(trades: list[dict]) -> dict:
    if not trades:
        return {"trades": 0, "win_rate": 0, "profit_factor": 0,
                "expectancy_r": 0, "total_r": 0, "max_dd_r": 0}
    wins = [t["r"] for t in trades if t["r"] > 0]
    losses = [t["r"] for t in trades if t["r"] < 0]
    eq = peak = dd = 0.0
    for t in trades:
        eq += t["r"]
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    pf = (sum(wins) / abs(sum(losses))) if losses else math.inf
    return {
        "trades": len(trades),
        "win_rate": round(100 * len(wins) / len(trades), 2),
        "profit_factor": round(pf, 3) if math.isfinite(pf) else "inf",
        "expectancy_r": round(statistics.fmean(t["r"] for t in trades), 4),
        "total_r": round(eq, 2),
        "max_dd_r": round(dd, 3),
    }


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def collect_candidates(series: dict[str, Series], context: Context) -> list[dict]:
    """Every ENTREE the engine would have produced, with its filter verdicts."""
    candidates: list[dict] = []
    evaluated = 0
    for symbol, s in series.items():
        m15 = s.m15
        if not m15:
            continue
        for i in range(250, len(m15.ts) - 1):
            close_time = m15.ts[i] + 900
            d1c, h4c, h1c = s.completed(close_time)
            if not d1c or not h1c or not h4c:
                continue
            if len(d1c.close) < 205 or len(h1c.close) < 205 or len(h4c.close) < 60:
                continue
            m15c = tail(m15, i + 1, M15_TAIL)
            if not m15c or len(m15c.close) < 60:
                continue

            strength, intraday, (regime_label, regime_score) = context.at(close_time)
            scanner.INTRADAY_STRENGTH.clear()
            scanner.INTRADAY_STRENGTH.update(intraday)
            scanner.INTRADAY_REGIME.clear()
            scanner.INTRADAY_REGIME.update({"label": regime_label, "score": regime_score})

            frames = {"d1": d1c, "h4": h4c, "h1": h1c, "m15": m15c}
            old_session = scanner.session_name
            scanner.session_name = lambda ts=m15.ts[i]: session_for(ts)
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
                "score": sig.score, "ts": m15.ts[i], "idx": i,
                "entry": m15.open[i + 1], "sl": sig.sl, "tp1": sig.tp1,
                "coherence": coherence["verdict"], "coherence_score": coherence["score"],
                "veto": coherence.get("veto"), "regime": regime_label,
                "sig": sig,
            })
    return candidates, evaluated


def run_variant(candidates: list[dict], series: dict[str, Series],
                use_coherence: bool, use_exposure: bool,
                use_throttle: bool = True) -> tuple[list[dict], dict]:
    """Replay the shortlist chronologically under one filter configuration.

    ``use_throttle`` reproduces the two production limiters the v1 harness
    ignored: at most ``MAX_ALERTS`` alerts per 15-minute scan, and a
    ``COOLDOWN``-minute mute per (pair, side, state). Without them the engine
    fires on nearly every bar, which inflates the trade count and makes any
    downstream filter look artificially selective.
    """
    dropped = {"coherence": 0, "veto": 0, "exposure": 0, "cooldown": 0, "quota": 0}
    open_trades: list[dict] = []
    trades: list[dict] = []
    last_sent: dict[tuple[str, str], int] = {}

    slots: dict[int, list[dict]] = {}
    for c in candidates:
        slots.setdefault(c["ts"] - (c["ts"] % 900), []).append(c)

    for slot in sorted(slots):
        batch = sorted(slots[slot], key=lambda x: x["score"], reverse=True)
        sent_this_slot = 0
        for c in batch:
            if use_coherence and c["coherence"] == forex_quality.INCOHERENT:
                dropped["veto" if c["veto"] else "coherence"] += 1
                continue
            if use_throttle:
                if sent_this_slot >= scanner.MAX_ALERTS:
                    dropped["quota"] += 1
                    continue
                key = (c["pair"], c["side"])
                if c["ts"] - last_sent.get(key, -10**9) < scanner.COOLDOWN * 60:
                    dropped["cooldown"] += 1
                    continue

            open_trades = [t for t in open_trades if t["exit_ts"] > c["ts"]]
            legs = v7._exposure(c["sig"])
            if use_exposure and v7.MAX_PER_CURRENCY > 0:
                held: dict = {}
                for t in open_trades:
                    for leg in t["legs"]:
                        held[leg] = held.get(leg, 0) + 1
                if any(held.get(leg, 0) >= v7.MAX_PER_CURRENCY for leg in legs):
                    dropped["exposure"] += 1
                    continue

            m15 = series[c["symbol"]].m15
            r, reason, exit_ts = resolve(m15, c["idx"] + 1, c["side"],
                                         c["entry"], c["sl"], c["tp1"])
            trade = {"pair": c["pair"], "side": c["side"], "score": c["score"],
                     "r": r, "exit": reason, "ts": c["ts"], "exit_ts": exit_ts,
                     "regime": c["regime"], "legs": legs}
            open_trades.append(trade)
            trades.append(trade)
            sent_this_slot += 1
            if use_throttle:
                last_sent[(c["pair"], c["side"])] = c["ts"]
    return trades, dropped


def main() -> int:
    started = time.time()
    series: dict[str, Series] = {}
    for symbol in PAIRS:
        try:
            d1 = fetch(symbol, "1d", "2y")
            h1 = fetch(symbol, "1h", "60d")
            m15 = fetch(symbol, "15m", "60d")
        except Exception as exc:
            print(f"{symbol}: données indisponibles ({exc})")
            continue
        if d1 and h1 and m15:
            series[symbol] = Series(symbol, d1, h1, m15)
    print(f"{len(series)}/{len(PAIRS)} paires chargées")

    context = Context(series)
    candidates, evaluated = collect_candidates(series, context)
    print(f"{evaluated} barres évaluées, {len(candidates)} entrées candidates")

    variants = {}
    for name, (coh, exp, thr) in {
        # Comparable to forex_backtest_v1: every ENTREE taken, no limiter.
        "0_toutes_les_entrees": (False, False, False),
        # What actually reached Telegram before the new filters.
        "1_production_avant": (False, False, True),
        "2_plus_coherence_et_veto": (True, False, True),
        "3_plus_exposition": (True, True, True),
    }.items():
        trades, dropped = run_variant(candidates, series, coh, exp, thr)
        by_pair: dict[str, list[dict]] = {}
        for t in trades:
            by_pair.setdefault(t["pair"], []).append(t)
        variants[name] = {
            "overall": stats(trades),
            "rejets": dropped,
            "by_pair": {k: stats(v) for k, v in sorted(by_pair.items())},
        }
        print(f"{name}: {variants[name]['overall']}")

    vetoed = [c for c in candidates if c["veto"]]
    report = {
        "engine": "Forex v7 complet — moteur + cohérence + veto régime refuge + exposition",
        "universe": len(series),
        "period": "60d M15 / 60d H1 / 2y D1",
        "bars_evaluated": evaluated,
        "candidates": len(candidates),
        "thresholds": {"SETUP_MIN": scanner.SETUP_MIN, "FINAL_MIN": scanner.FINAL_MIN,
                       "MAX_PER_CCY": v7.MAX_PER_CURRENCY,
                       "REGIME_MIN": scanner.REGIME_MIN},
        "variants": variants,
        "veto_regime": {
            "count": len(vetoed),
            "r_evite": round(sum(
                resolve(series[c["symbol"]].m15, c["idx"] + 1, c["side"],
                        c["entry"], c["sl"], c["tp1"])[0] for c in vetoed), 2),
        },
        "notes": [
            "Aucun spread ni commission modélisé — le réel est moins bon.",
            "Taux directeurs et news historiques exclus (sources point-in-time).",
            "Cooldown et MAX_ALERTS non modélisés.",
            "SL et TP touchés sur la même bougie comptés -1R.",
        ],
        "runtime_s": round(time.time() - started, 1),
    }
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report["variants"], indent=2, ensure_ascii=False))
    print(json.dumps(report["veto_regime"], indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
