"""Forex Intraday Scanner — D1/H4/H1/M15

Dedicated FX engine. Daily/H4 establish context, H1 defines setup,
M15 is the entry trigger. Uses DXY, US rates, VIX/SPY, currency strength,
ATR volatility, London/NY sessions, and structure-based R:R.
Alerting only; no broker orders are executed.
"""
from __future__ import annotations

import os
import time
import logging
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from statistics import mean

import requests
import telegram_signals as base

LOG = logging.getLogger("forex-intraday")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart"
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 T212Forex/1.0", "Accept": "application/json"})

PAIRS = {
    "EURUSD=X": ("EUR", "USD", "EUR/USD"),
    "GBPUSD=X": ("GBP", "USD", "GBP/USD"),
    "USDJPY=X": ("USD", "JPY", "USD/JPY"),
    "USDCHF=X": ("USD", "CHF", "USD/CHF"),
    "AUDUSD=X": ("AUD", "USD", "AUD/USD"),
    "NZDUSD=X": ("NZD", "USD", "NZD/USD"),
    "USDCAD=X": ("USD", "CAD", "USD/CAD"),
    "EURGBP=X": ("EUR", "GBP", "EUR/GBP"),
    "EURJPY=X": ("EUR", "JPY", "EUR/JPY"),
    "GBPJPY=X": ("GBP", "JPY", "GBP/JPY"),
    "AUDJPY=X": ("AUD", "JPY", "AUD/JPY"),
    "CADJPY=X": ("CAD", "JPY", "CAD/JPY"),
}

DXY = "DX-Y.NYB"
US10Y = "^TNX"
VIX = "^VIX"
SPY = "SPY"
MAX_ALERTS = int(os.getenv("FOREX_MAX_ALERTS", "3"))
FINAL_MIN = int(os.getenv("FOREX_FINAL_MIN", "72"))
RISK_PCT = float(os.getenv("FOREX_RISK_PCT", "0.5"))
COOLDOWN_MIN = int(os.getenv("FOREX_COOLDOWN_MIN", "240"))

@dataclass
class Bars:
    ts: list[int]; open: list[float]; high: list[float]; low: list[float]; close: list[float]; volume: list[float]

@dataclass
class FxSignal:
    symbol: str; pair: str; side: str; score: int; price: float
    stop: float; tp1: float; tp2: float; rr1: float
    d1: str; h4: str; h1: str; m15: str
    session: str; macro: str; dxy_bias: str; currency_bias: str
    reasons: list[str]


def fetch(symbol: str, interval: str, range_: str) -> Bars | None:
    try:
        r = S.get(f"{YAHOO}/{symbol}", params={"range": range_, "interval": interval, "includePrePost": "false", "events": "div,splits"}, timeout=15)
        r.raise_for_status()
        item = r.json()["chart"]["result"][0]
        ts = item.get("timestamp", [])
        q = item["indicators"]["quote"][0]
        rows = []
        for i, t in enumerate(ts):
            vals = (q["open"][i], q["high"][i], q["low"][i], q["close"][i], q["volume"][i])
            if all(v is not None for v in vals):
                rows.append((int(t), *map(float, vals)))
        if len(rows) < 60:
            return None
        return Bars([r[0] for r in rows], [r[1] for r in rows], [r[2] for r in rows], [r[3] for r in rows], [r[4] for r in rows], [r[5] for r in rows])
    except Exception as e:
        LOG.warning("%s %s: %s", symbol, interval, e)
        return None


def ema(v: list[float], p: int) -> list[float]:
    if not v: return []
    k = 2 / (p + 1); out = [v[0]]
    for x in v[1:]: out.append(x * k + out[-1] * (1 - k))
    return out


def atr(d: Bars, p: int = 14) -> float:
    if len(d.close) < p + 1: return 0.0
    tr = []
    prev = d.close[0]
    for h, l, c in zip(d.high[1:], d.low[1:], d.close[1:]):
        tr.append(max(h - l, abs(h - prev), abs(l - prev))); prev = c
    return mean(tr[-p:]) if len(tr) >= p else 0.0


def ret(d: Bars | None, n: int) -> float:
    if not d or len(d.close) <= n: return 0.0
    return (d.close[-1] / d.close[-n - 1] - 1) * 100


def trend(d: Bars) -> int:
    e20, e50, e200 = ema(d.close, 20), ema(d.close, 50), ema(d.close, 200)
    if len(e200) < 2: return 0
    p = d.close[-1]
    if p > e20[-1] > e50[-1] > e200[-1]: return 2
    if p > e50[-1] > e200[-1]: return 1
    if p < e20[-1] < e50[-1] < e200[-1]: return -2
    if p < e50[-1] < e200[-1]: return -1
    return 0


def session_name() -> str:
    now = datetime.now(timezone.utc)
    h = now.hour + now.minute / 60
    if 7 <= h < 12: return "LONDRES"
    if 12 <= h < 17: return "LONDRES + NEW YORK"
    if 17 <= h < 21: return "NEW YORK"
    return "HORS_SESSION"


def macro_context(data: dict[str, Bars | None]) -> tuple[str, str]:
    dxy, us10, vix, spy = data.get(DXY), data.get(US10Y), data.get(VIX), data.get(SPY)
    score = 0; reasons = []
    if ret(dxy, 20) > 1.5: score -= 2; reasons.append("DXY fort")
    elif ret(dxy, 20) < -1.5: score += 2; reasons.append("DXY faible")
    if ret(us10, 20) > 2: score -= 1; reasons.append("taux US en hausse")
    elif ret(us10, 20) < -2: score += 1; reasons.append("taux US en baisse")
    if ret(vix, 10) > 8: score -= 1; reasons.append("VIX haut")
    elif ret(vix, 10) < -8: score += 1; reasons.append("VIX en baisse")
    if ret(spy, 20) > 1: score += 1; reasons.append("risk-on actions")
    elif ret(spy, 20) < -1: score -= 1; reasons.append("risk-off actions")
    regime = "RISK-ON" if score >= 2 else "RISK-OFF" if score <= -2 else "MIXTE"
    return regime, " • ".join(reasons) or "macro neutre"


def currency_strength(pair_data: dict[str, Bars | None]) -> dict[str, float]:
    strengths: dict[str, list[float]] = {x: [] for x in ["EUR", "GBP", "USD", "JPY", "CHF", "AUD", "NZD", "CAD"]}
    for sym, (base_ccy, quote_ccy, _) in PAIRS.items():
        d = pair_data.get(sym)
        if not d: continue
        r = ret(d, 20)
        strengths[base_ccy].append(r); strengths[quote_ccy].append(-r)
    return {k: mean(v) if v else 0.0 for k, v in strengths.items()}


def score_pair(sym: str, frames: dict[str, Bars | None], strength: dict[str, float], macro_regime: str, macro_reason: str) -> FxSignal | None:
    base_ccy, quote_ccy, pair = PAIRS[sym]
    d1, h4, h1, m15 = frames.get("1d"), frames.get("1h4"), frames.get("1h"), frames.get("15m")
    if not all((d1, h4, h1, m15)): return None
    td1, th4, th1 = trend(d1), trend(h4), trend(h1)
    atr15 = atr(m15, 14); atr_h1 = atr(h1, 14)
    if atr15 <= 0 or atr_h1 <= 0: return None
    p = m15.close[-1]
    strong = strength.get(base_ccy, 0) - strength.get(quote_ccy, 0)
    dxy_r = ret(frames.get("DXY"), 20)
    dxy_bias = "DXY_BULL" if dxy_r > 1.5 else "DXY_BEAR" if dxy_r < -1.5 else "DXY_NEUTRAL"
    long_score = short_score = 0; lr=[]; sr=[]
    if td1 > 0: long_score += 20; lr.append("D1 haussier")
    if td1 < 0: short_score += 20; sr.append("D1 baissier")
    if th4 > 0: long_score += 18; lr.append("H4 haussier")
    if th4 < 0: short_score += 18; sr.append("H4 baissier")
    if th1 > 0: long_score += 16; lr.append("H1 haussier")
    if th1 < 0: short_score += 16; sr.append("H1 baissier")
    if strong > 1.0: long_score += 12; lr.append(f"force {base_ccy}/{quote_ccy}")
    if strong < -1.0: short_score += 12; sr.append(f"faiblesse {base_ccy}/{quote_ccy}")
    if "USD" in (base_ccy, quote_ccy):
        usd_pref = 1 if dxy_r < -1.5 else -1 if dxy_r > 1.5 else 0
        if base_ccy == "USD": usd_pref *= -1
        if quote_ccy == "USD": usd_pref *= -1
        if usd_pref > 0: long_score += 8; lr.append("DXY confirme")
        if usd_pref < 0: short_score += 8; sr.append("DXY confirme")
    if macro_regime == "RISK-ON":
        if base_ccy in ("AUD", "NZD", "CAD"): long_score += 4; lr.append("macro pro-cyclique")
        if quote_ccy in ("JPY", "CHF"): long_score += 3; lr.append("macro pro-cyclique")
    if macro_regime == "RISK-OFF":
        if quote_ccy in ("JPY", "CHF"): short_score += 4; sr.append("macro défensif")
        if base_ccy in ("JPY", "CHF"): long_score += 3; lr.append("macro défensif")
    side = "BUY" if long_score > short_score else "SELL"
    score = max(long_score, short_score)
    if score < FINAL_MIN: return None
    # M15 trigger = breakout/reclaim with volume proxy where available.
    recent_hi = max(m15.high[-9:-1]); recent_lo = min(m15.low[-9:-1])
    e20 = ema(m15.close, 20)[-1]
    if side == "BUY":
        trigger = p > recent_hi or (p > e20 and m15.close[-1] > m15.close[-2] > m15.close[-3])
        if not trigger: return None
        stop = min(recent_lo - 0.35 * atr15, p - 1.15 * atr15)
        risk = max(p - stop, atr15)
        tp1 = p + 1.8 * risk; tp2 = p + 3.0 * risk
        rr = (tp1 - p) / risk if risk else 0
    else:
        trigger = p < recent_lo or (p < e20 and m15.close[-1] < m15.close[-2] < m15.close[-3])
        if not trigger: return None
        stop = max(recent_hi + 0.35 * atr15, p + 1.15 * atr15)
        risk = max(stop - p, atr15)
        tp1 = p - 1.8 * risk; tp2 = p - 3.0 * risk
        rr = (p - tp1) / risk if risk else 0
    session = session_name()
    if session == "HORS_SESSION": return None
    return FxSignal(sym, pair, side, min(100, score + (5 if session != "HORS_SESSION" else 0)), p, stop, tp1, tp2, rr,
                    "BULLISH" if td1>0 else "BEARISH" if td1<0 else "MIXTE",
                    "BULLISH" if th4>0 else "BEARISH" if th4<0 else "MIXTE",
                    "BULLISH" if th1>0 else "BEARISH" if th1<0 else "MIXTE",
                    "CONFIRMÉ", session, macro_regime, dxy_bias,
                    f"{base_ccy} {'+' if strong>=0 else ''}{strong:.1f} vs {quote_ccy}",
                    (lr if side=="BUY" else sr))


def format_signal(s: FxSignal) -> str:
    icon = "🟢" if s.side == "BUY" else "🔴"
    action = "ACHAT" if s.side == "BUY" else "VENTE"
    return (f"{icon} SIGNAL FOREX INTRADAY — {s.pair}\n━━━━━━━━━━━━━━━━━━\n"
            f"STRATÉGIE : D1 + H4 + H1 + M15\nDirection : {action}\nScore : {s.score}/100\n"
            f"Entrée : {s.price:.5f}\nSL : {s.stop:.5f}\nTP1 : {s.tp1:.5f}\nTP2 : {s.tp2:.5f}\n"
            f"R:R TP1 : 1:{s.rr1:.1f}\nD1 : {s.d1} | H4 : {s.h4} | H1 : {s.h1} | M15 : {s.m15}\n"
            f"DXY : {s.dxy_bias}\nForce relative : {s.currency_bias}\nMacro : {s.macro}\nSession : {s.session}\n"
            f"Confluence : {' • '.join(s.reasons[:8])}\n"
            f"⚠️ Analyse uniquement — aucun ordre Forex n'est exécuté.")


def main() -> int:
    if not base.TELEGRAM_BOT_TOKEN or not base.TELEGRAM_CHAT_ID: return 2
    symbols = list(PAIRS)
    all_symbols = symbols + [DXY, US10Y, VIX, SPY]
    frames: dict[str, dict[str, Bars | None]] = {s:{} for s in symbols}
    market: dict[str, Bars | None] = {}
    jobs=[]
    with ThreadPoolExecutor(max_workers=12) as ex:
        for s in symbols:
            for interval, rng in (("1d","2y"),("1h","6mo"),("15m","10d")):
                jobs.append((s, interval, rng, ex.submit(fetch, s, interval, rng)))
            jobs.append((s, "1h4", "1y", ex.submit(fetch, s, "1h", "1y")))
        for sym in [DXY, US10Y, VIX, SPY]:
            jobs.append((sym, "macro", "1y", ex.submit(fetch, sym, "1d", "1y")))
        for s, interval, _, fut in jobs:
            try:
                d = fut.result()
            except Exception:
                d = None
            if s in frames: frames[s][interval] = d
            else: market[s] = d
    strengths = currency_strength({s: frames[s].get("1d") for s in symbols})
    macro_regime_name, macro_reason = macro_context(market)
    candidates=[]
    for s in symbols:
        f = dict(frames[s]); f.update(market); sig=score_pair(s, f, strengths, macro_regime_name, macro_reason)
        if sig: candidates.append(sig)
    candidates.sort(key=lambda x:x.score, reverse=True)
    state = base.load_state(); now=time.time(); sent=0
    for sig in candidates[:MAX_ALERTS]:
        key=f"FX:{sig.pair}:{sig.side}"; last=float(state.get(key,{}).get("sent_at",0))
        if now-last < COOLDOWN_MIN*60: continue
        if base.telegram_send(format_signal(sig)):
            state[key]={"sent_at":now,"price":sig.price,"score":sig.score}; sent += 1
    base.save_state(state)
    base.telegram_send(f"💱 Scan FOREX D1+H4+H1+M15: {len(candidates)} signaux confirmés | envoyés {sent} | Macro {macro_regime_name} | DXY {('fort' if ret(market.get(DXY),20)>1.5 else 'faible' if ret(market.get(DXY),20)<-1.5 else 'neutre')}")
    LOG.info("Forex: candidates=%d sent=%d", len(candidates), sent)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
