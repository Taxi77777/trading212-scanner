from __future__ import annotations

"""Forex Intraday Scanner v3.

D1/H4 establish regime, H1 confirms structure, M15 times entry.
Adds DXY, market regime, multi-horizon currency strength, liquidity/sweeps,
volatility regime, correlation proxies, configurable macro-calendar blackout,
and SETUP vs ENTRY states. Alerting only; no broker orders.
"""

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import mean
from urllib.parse import urlparse

import requests
import telegram_signals as base

LOG = logging.getLogger("forex-v3")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0 T212Forex/3.0", "Accept": "application/json"})

PAIRS = {
    "EURUSD=X": ("EUR", "USD", "EUR/USD"), "GBPUSD=X": ("GBP", "USD", "GBP/USD"),
    "USDJPY=X": ("USD", "JPY", "USD/JPY"), "USDCHF=X": ("USD", "CHF", "USD/CHF"),
    "AUDUSD=X": ("AUD", "USD", "AUD/USD"), "NZDUSD=X": ("NZD", "USD", "NZD/USD"),
    "USDCAD=X": ("USD", "CAD", "USD/CAD"), "EURGBP=X": ("EUR", "GBP", "EUR/GBP"),
    "EURJPY=X": ("EUR", "JPY", "EUR/JPY"), "GBPJPY=X": ("GBP", "JPY", "GBP/JPY"),
    "AUDJPY=X": ("AUD", "JPY", "AUD/JPY"), "CADJPY=X": ("CAD", "JPY", "CAD/JPY"),
}
DXY, US10Y, VIX, SPY = "DX-Y.NYB", "^TNX", "^VIX", "SPY"
FINAL_MIN = int(os.getenv("FOREX_FINAL_MIN", "68"))
SETUP_MIN = int(os.getenv("FOREX_SETUP_MIN", "60"))
MAX_ALERTS = int(os.getenv("FOREX_MAX_ALERTS", "3"))
COOLDOWN = int(os.getenv("FOREX_COOLDOWN_MIN", "240"))
NEWS_BLOCK_MIN = int(os.getenv("FOREX_NEWS_BLOCK_MIN", "30"))
CALENDAR_URL = os.getenv("FOREX_CALENDAR_URL", "")

@dataclass
class Bars:
    ts: list[int]
    open: list[float]
    high: list[float]
    low: list[float]
    close: list[float]
    volume: list[float]

@dataclass
class FxSignal:
    pair: str
    symbol: str
    side: str
    state: str
    score: int
    price: float
    sl: float
    tp1: float
    tp2: float
    rr: float
    d1: str
    h4: str
    h1: str
    m15: str
    dxy: str
    macro: str
    strength: str
    vol_regime: str
    session: str
    liquidity: str
    correlation: str
    news: str
    reasons: list[str]


def fetch(symbol: str, interval: str, range_: str) -> Bars | None:
    try:
        r = SESSION.get(f"{YAHOO}/{symbol}", params={"range": range_, "interval": interval, "includePrePost": "false", "events": "div,splits"}, timeout=15)
        r.raise_for_status()
        result = r.json()["chart"]["result"][0]
        ts = result.get("timestamp", [])
        q = result["indicators"]["quote"][0]
        rows = []
        for i, t in enumerate(ts):
            vals = (q["open"][i], q["high"][i], q["low"][i], q["close"][i], q["volume"][i])
            if all(v is not None for v in vals):
                rows.append((int(t), *map(float, vals)))
        if len(rows) < 60:
            return None
        return Bars([x[0] for x in rows], [x[1] for x in rows], [x[2] for x in rows], [x[3] for x in rows], [x[4] for x in rows], [x[5] for x in rows])
    except Exception as exc:
        LOG.warning("%s %s: %s", symbol, interval, exc)
        return None


def resample_h4(h1: Bars | None) -> Bars | None:
    if not h1:
        return None
    buckets: dict[int, list[int]] = {}
    for i, t in enumerate(h1.ts):
        key = t - (t % 14400)
        buckets.setdefault(key, []).append(i)
    rows = []
    for key in sorted(buckets):
        idx = buckets[key]
        if len(idx) < 3:
            continue
        rows.append((key, h1.open[idx[0]], max(h1.high[i] for i in idx), min(h1.low[i] for i in idx), h1.close[idx[-1]], sum(h1.volume[i] for i in idx)))
    if len(rows) < 60:
        return None
    return Bars([x[0] for x in rows], [x[1] for x in rows], [x[2] for x in rows], [x[3] for x in rows], [x[4] for x in rows], [x[5] for x in rows])


def ema(v: list[float], p: int) -> list[float]:
    if not v:
        return []
    k = 2 / (p + 1)
    out = [v[0]]
    for x in v[1:]:
        out.append(x * k + out[-1] * (1 - k))
    return out


def atr(d: Bars | None, p: int = 14) -> float:
    if not d or len(d.close) < p + 1:
        return 0.0
    tr, prev = [], d.close[0]
    for h, l, c in zip(d.high[1:], d.low[1:], d.close[1:]):
        tr.append(max(h - l, abs(h - prev), abs(l - prev)))
        prev = c
    return mean(tr[-p:])


def ret(d: Bars | None, n: int) -> float:
    return ((d.close[-1] / d.close[-n - 1]) - 1) * 100 if d and len(d.close) > n else 0.0


def trend(d: Bars | None) -> int:
    if not d or len(d.close) < 205:
        return 0
    e20, e50, e200 = ema(d.close, 20), ema(d.close, 50), ema(d.close, 200)
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


def volatility_regime(d: Bars | None) -> str:
    a = atr(d)
    if not d or a <= 0 or len(d.close) < 60:
        return "INCONNU"
    now = atr(d)
    hist = []
    for i in range(30, len(d.close)):
        sub = Bars(d.ts[:i], d.open[:i], d.high[:i], d.low[:i], d.close[:i], d.volume[:i])
        hist.append(atr(sub))
    med = mean(hist[-30:]) if hist else now
    ratio = now / med if med else 1.0
    if ratio > 1.8: return "EXPLOSIVE"
    if ratio > 1.25: return "ELEVEE"
    if ratio < 0.7: return "FAIBLE"
    return "NORMALE"


def currency_strength(ds: dict[str, Bars | None]) -> dict[str, float]:
    horizons = (5, 20, 60)
    out = {c: [] for c in ("EUR", "GBP", "USD", "JPY", "CHF", "AUD", "NZD", "CAD")}
    for sym, (a, b, _) in PAIRS.items():
        d = ds.get(sym)
        if not d:
            continue
        r = mean(ret(d, n) for n in horizons)
        out[a].append(r); out[b].append(-r)
    return {k: mean(v) if v else 0.0 for k, v in out.items()}


def liquidity_state(m15: Bars, d1: Bars) -> tuple[str, int]:
    p = m15.close[-1]
    prev_hi, prev_lo = d1.high[-2], d1.low[-2]
    recent_hi, recent_lo = max(m15.high[-20:-1]), min(m15.low[-20:-1])
    swept_hi = m15.high[-1] > recent_hi and p < recent_hi
    swept_lo = m15.low[-1] < recent_lo and p > recent_lo
    if swept_lo: return "SWEEP_LOW_RECLAIM", 3
    if swept_hi: return "SWEEP_HIGH_REJECT", 3
    if p > prev_hi: return "ABOVE_PREVIOUS_DAY_HIGH", 2
    if p < prev_lo: return "BELOW_PREVIOUS_DAY_LOW", 2
    return "RANGE", 0


def correlation_bias(pair: str, frames: dict[str, Bars | None]) -> tuple[str, int]:
    a, b, _ = PAIRS[pair]
    dxy_r = ret(frames.get(DXY), 20)
    us10_r = ret(frames.get(US10Y), 20)
    score = 0
    if "USD" in (a, b):
        usd_long = dxy_r > 1.5
        if a == "USD" and usd_long: score += 2
        if b == "USD" and usd_long: score -= 2
        if a == "USD" and dxy_r < -1.5: score -= 2
        if b == "USD" and dxy_r < -1.5: score += 2
    if pair in ("USDJPY=X", "EURJPY=X", "GBPJPY=X", "AUDJPY=X", "CADJPY=X"):
        if us10_r > 2: score += 1 if a == "USD" else 0
        if us10_r < -2: score -= 1 if a == "USD" else 0
    label = "CONFIRMÉ" if score > 0 else "CONTRE" if score < 0 else "NEUTRE"
    return label, score


def macro_regime(market: dict[str, Bars | None]) -> tuple[str, str]:
    dxy, us10, vix, spy = market.get(DXY), market.get(US10Y), market.get(VIX), market.get(SPY)
    s = 0; reasons = []
    if ret(dxy, 20) > 1.5: s -= 2; reasons.append("DXY fort")
    elif ret(dxy, 20) < -1.5: s += 2; reasons.append("DXY faible")
    if ret(us10, 20) > 2: s -= 1; reasons.append("taux US montent")
    elif ret(us10, 20) < -2: s += 1; reasons.append("taux US baissent")
    if ret(vix, 10) > 8: s -= 1; reasons.append("VIX haut")
    elif ret(vix, 10) < -8: s += 1; reasons.append("VIX baisse")
    if ret(spy, 20) > 1: s += 1; reasons.append("risk-on")
    elif ret(spy, 20) < -1: s -= 1; reasons.append("risk-off")
    return ("RISK-ON" if s >= 2 else "RISK-OFF" if s <= -2 else "MIXTE"), " • ".join(reasons) or "macro neutre"


def calendar_events() -> list[dict]:
    if not CALENDAR_URL:
        return []
    try:
        u = urlparse(CALENDAR_URL)
        if u.scheme not in ("http", "https") or not u.netloc:
            return []
        r = SESSION.get(CALENDAR_URL, timeout=10)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else data.get("events", [])
    except Exception as exc:
        LOG.warning("calendar unavailable: %s", exc)
        return []


def event_risk(pair: str, events: list[dict]) -> tuple[str, bool]:
    now = time.time(); base_ccy, quote_ccy, _ = PAIRS[pair]
    for ev in events:
        impact = str(ev.get("impact", ev.get("importance", ""))).lower()
        if impact not in ("high", "3", "red"):
            continue
        ccy = str(ev.get("currency", ev.get("ccy", ""))).upper()
        when = ev.get("timestamp", ev.get("time"))
        try:
            ts = float(when)
            if ts > 1e12: ts /= 1000
        except (TypeError, ValueError):
            continue
        if 0 <= ts - now <= NEWS_BLOCK_MIN * 60 and ccy in (base_ccy, quote_ccy):
            return f"HIGH IMPACT {ccy} < {NEWS_BLOCK_MIN}m", True
    return "AUCUN HIGH IMPACT CONFIGURE", False


def build_signal(pair: str, frames: dict[str, Bars | None], strength: dict[str, float], macro: str, macro_reason: str, news: str, news_block: bool) -> FxSignal | None:
    a, b, label = PAIRS[pair]
    d1, h4, h1, m15 = frames.get("d1"), frames.get("h4"), frames.get("h1"), frames.get("m15")
    if not all((d1, h4, h1, m15)): return None
    td, th4, th1 = trend(d1), trend(h4), trend(h1)
    strong = strength.get(a, 0) - strength.get(b, 0)
    dxy_r = ret(frames.get(DXY), 20)
    dxy = "BULL" if dxy_r > 1.5 else "BEAR" if dxy_r < -1.5 else "NEUTRAL"
    long = short = 0; lr, sr = [], []
    if td > 0: long += 20; lr.append("D1 haussier")
    if td < 0: short += 20; sr.append("D1 baissier")
    if th4 > 0: long += 18; lr.append("H4 haussier")
    if th4 < 0: short += 18; sr.append("H4 baissier")
    if th1 > 0: long += 14; lr.append("H1 haussier")
    if th1 < 0: short += 14; sr.append("H1 baissier")
    if strong > 1.0: long += 14; lr.append(f"{a} fort / {b} faible")
    if strong < -1.0: short += 14; sr.append(f"{b} fort / {a} faible")
    corr_label, corr_score = correlation_bias(pair, frames)
    if corr_score > 0: long += 5; lr.append("corrélation confirme")
    if corr_score < 0: short += 5; sr.append("corrélation confirme")
    liq_label, liq_score = liquidity_state(m15, d1)
    side = "BUY" if long > short else "SELL"
    raw = max(long, short)
    score = raw + abs(corr_score)
    if macro == "RISK-ON" and a in ("AUD", "NZD", "CAD"): score += 3 if side == "BUY" else 0
    if macro == "RISK-OFF" and b in ("JPY", "CHF"): score += 3 if side == "SELL" else 0
    if news_block:
        return None
    if score < SETUP_MIN: return None
    sess = session_name()
    if sess == "HORS_SESSION": return None
    vol = volatility_regime(m15)
    if vol == "EXPLOSIVE": score -= 3
    if vol == "FAIBLE": score -= 2
    p = m15.close[-1]; a15 = atr(m15); a1 = atr(h1)
    if a15 <= 0 or a1 <= 0: return None
    e20 = ema(m15.close, 20)[-1]
    hi, lo = max(m15.high[-12:-1]), min(m15.low[-12:-1])
    trigger = (p > hi or (p > e20 and m15.close[-1] > m15.close[-2])) if side == "BUY" else (p < lo or (p < e20 and m15.close[-1] < m15.close[-2]))
    state = "ENTREE" if trigger and score >= FINAL_MIN else "SETUP"
    if not trigger:
        # Still alert strong setups; entry remains pending M15 confirmation.
        state = "SETUP"
    if side == "BUY":
        sl = min(lo - 0.35 * a15, p - 1.1 * a15)
        risk = max(p - sl, a15)
        tp1, tp2 = p + 1.7 * risk, p + 2.8 * risk
    else:
        sl = max(hi + 0.35 * a15, p + 1.1 * a15)
        risk = max(sl - p, a15)
        tp1, tp2 = p - 1.7 * risk, p - 2.8 * risk
    rr = 1.7
    final_score = max(0, min(100, int(score + (liq_score if trigger else 0))))
    reasons = lr if side == "BUY" else sr
    return FxSignal(label, pair, side, state, final_score, p, sl, tp1, tp2, rr,
                    "BULLISH" if td > 0 else "BEARISH" if td < 0 else "MIXTE",
                    "BULLISH" if th4 > 0 else "BEARISH" if th4 < 0 else "MIXTE",
                    "BULLISH" if th1 > 0 else "BEARISH" if th1 < 0 else "MIXTE",
                    "CONFIRME" if trigger else "EN_ATTENTE",
                    dxy, macro, f"{a} {strong:+.1f} vs {b}", vol, sess, liq_label, corr_label, news, reasons)


def format_signal(s: FxSignal) -> str:
    icon = "🟢" if s.side == "BUY" else "🔴"
    action = "ACHAT" if s.side == "BUY" else "VENTE"
    return (
        f"{icon} SIGNAL FOREX {s.state} — {s.pair}\n━━━━━━━━━━━━━━━━━━\n"
        f"STRATÉGIE : D1 + H4 + H1 + M15\nDirection : {action}\nScore : {s.score}/100\n"
        f"Entrée : {s.price:.5f}\nSL : {s.sl:.5f}\nTP1 : {s.tp1:.5f}\nTP2 : {s.tp2:.5f}\nR:R TP1 : 1:{s.rr:.1f}\n"
        f"D1 : {s.d1} | H4 : {s.h4} | H1 : {s.h1} | M15 : {s.m15}\n"
        f"DXY : {s.dxy}\nForce multi-horizon : {s.strength}\nVolatilité : {s.vol_regime}\n"
        f"Liquidité : {s.liquidity}\nCorrélation : {s.correlation}\nMacro : {s.macro}\nSession : {s.session}\nNews : {s.news}\n"
        f"Confluence : {' • '.join(s.reasons[:8])}\n⚠️ Analyse uniquement — aucun ordre Forex n'est exécuté."
    )


def main() -> int:
    if not base.TELEGRAM_BOT_TOKEN or not base.TELEGRAM_CHAT_ID:
        return 2
    pairs = list(PAIRS)
    data: dict[str, dict[str, Bars | None]] = {s: {} for s in pairs}
    market: dict[str, Bars | None] = {}
    jobs = []
    with ThreadPoolExecutor(max_workers=12) as ex:
        for s in pairs:
            jobs += [(s, "d1", ex.submit(fetch, s, "1d", "2y")), (s, "h1", ex.submit(fetch, s, "1h", "6mo")), (s, "m15", ex.submit(fetch, s, "15m", "10d"))]
        for s in (DXY, US10Y, VIX, SPY):
            jobs.append((s, "macro", ex.submit(fetch, s, "1d", "1y")))
        for s, key, fut in jobs:
            try: d = fut.result()
            except Exception: d = None
            (data[s].__setitem__(key, d) if s in data else market.__setitem__(s, d))
    for s in pairs:
        data[s]["h4"] = resample_h4(data[s].get("h1"))
        data[s].update(market)
    strength = currency_strength({s: data[s].get("d1") for s in pairs})
    macro, macro_reason = macro_regime(market)
    events = calendar_events()
    candidates = []
    setup_count = entry_count = blocked_news = 0
    for s in pairs:
        news, blocked = event_risk(PAIRS[s][2], events)
        if blocked: blocked_news += 1
        sig = build_signal(s, data[s], strength, macro, macro_reason, news, blocked)
        if sig:
            candidates.append(sig)
            setup_count += 1
            entry_count += int(sig.state == "ENTREE")
    candidates.sort(key=lambda x: x.score, reverse=True)
    state = base.load_state(); now = time.time(); sent = 0
    for sig in candidates[:MAX_ALERTS]:
        key = f"FXV3:{sig.pair}:{sig.side}:{sig.state}"
        if now - float(state.get(key, {}).get("sent_at", 0)) < COOLDOWN * 60:
            continue
        if base.telegram_send(format_signal(sig)):
            state[key] = {"sent_at": now, "price": sig.price, "score": sig.score}
            sent += 1
    base.save_state(state)
    base.telegram_send(
        f"💱 Scan FOREX v3 D1+H4+H1+M15: setups {setup_count} | entrées {entry_count} | envoyés {sent} | news bloqués {blocked_news} | Macro {macro}"
    )
    LOG.info("Forex v3: setups=%d entries=%d sent=%d", setup_count, entry_count, sent)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
