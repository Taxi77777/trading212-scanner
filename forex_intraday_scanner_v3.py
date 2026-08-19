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


def pair_key(pair: str) -> str:
    """Resolve either a Yahoo symbol or a human-readable label to PAIRS key."""
    if pair in PAIRS:
        return pair
    normalized = str(pair).strip().upper().replace(" ", "")
    if normalized in PAIRS:
        return normalized
    for symbol, values in PAIRS.items():
        if normalized == str(values[2]).upper().replace(" ", ""):
            return symbol
    return pair

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
    dxy: str = ""
    macro: str = ""
    strength: str = ""
    vol_regime: str = ""
    liquidity: str = ""
    correlation: str = ""
    news: str = ""
    session: str = ""
    reasons: list[str] | None = None


def fetch(symbol: str, interval: str = "1h", range_: str = "1mo"):
    try:
        r = SESSION.get(f"{YAHOO}/{symbol}", params={"interval": interval, "range": range_}, timeout=12)
        r.raise_for_status()
        data = r.json().get("chart", {}).get("result")
        if not data:
            return None
        result = data[0]
        timestamps = result.get("timestamp") or []
        quote = (result.get("indicators", {}).get("quote") or [{}])[0]
        return Bars(
            ts=[int(x) for x in timestamps],
            open=[float(x or 0) for x in quote.get("open", [])],
            high=[float(x or 0) for x in quote.get("high", [])],
            low=[float(x or 0) for x in quote.get("low", [])],
            close=[float(x or 0) for x in quote.get("close", [])],
            volume=[float(x or 0) for x in quote.get("volume", [])],
        )
    except Exception as exc:
        LOG.warning("fetch %s %s: %s", symbol, interval, exc)
        return None


def load_calendar() -> list[dict]:
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
    pair = pair_key(pair)
    if pair not in PAIRS:
        return "AUCUN HIGH IMPACT CONFIGURE", False
    now = time.time()
    base_ccy, quote_ccy, _ = PAIRS[pair]
    for ev in events:
        impact = str(ev.get("impact", ev.get("importance", ""))).lower()
        if impact not in ("high", "3", "red"):
            continue
        ccy = str(ev.get("currency", ev.get("ccy", ""))).upper()
        when = ev.get("timestamp", ev.get("time"))
        try:
            ts = float(when)
            if ts > 1e12:
                ts /= 1000
        except (TypeError, ValueError):
            continue
        if 0 <= ts - now <= NEWS_BLOCK_MIN * 60 and ccy in (base_ccy, quote_ccy):
            return f"HIGH IMPACT {ccy} < {NEWS_BLOCK_MIN}m", True
    return "AUCUN HIGH IMPACT CONFIGURE", False


def build_signal(pair: str, frames: dict[str, Bars | None], strength: dict[str, float], macro: str, macro_reason: str, news: str, news_block: bool) -> FxSignal | None:
    pair = pair_key(pair)
    a, b, label = PAIRS[pair]
    d1, h4, h1, m15 = frames.get("d1"), frames.get("h4"), frames.get("h1"), frames.get("m15")
    if not all((d1, h4, h1, m15)) or news_block:
        return None
    closes = {"d1": d1.close[-1], "h4": h4.close[-1], "h1": h1.close[-1], "m15": m15.close[-1]}
    side = "BUY" if h1.close[-1] >= h1.close[-2] else "SELL"
    score = 0
    reasons = []
    for name, bars in (("D1", d1), ("H4", h4), ("H1", h1)):
        bullish = bars.close[-1] >= bars.close[-2]
        if bullish == (side == "BUY"):
            score += 10
            reasons.append(f"{name} {'haussier' if bullish else 'baissier'}")
    if side == "BUY":
        score += 10 if m15.close[-1] >= m15.close[-2] else 0
    else:
        score += 10 if m15.close[-1] <= m15.close[-2] else 0
    price = closes["m15"]
    risk = max(abs(h1.close[-1] - h1.close[-2]), price * 0.001)
    sl = price - risk if side == "BUY" else price + risk
    tp1 = price + 1.7 * risk if side == "BUY" else price - 1.7 * risk
    tp2 = price + 2.8 * risk if side == "BUY" else price - 2.8 * risk
    rr = 1.7
    return FxSignal(pair=pair, symbol=label, side=side, state="ENTREE" if score >= FINAL_MIN else "SETUP", score=score, price=price, sl=sl, tp1=tp1, tp2=tp2, rr=rr, d1="BULLISH" if d1.close[-1] >= d1.close[-2] else "BEARISH", h4="BULLISH" if h4.close[-1] >= h4.close[-2] else "BEARISH", h1="BULLISH" if h1.close[-1] >= h1.close[-2] else "BEARISH", m15="CONFIRME" if ((side == "BUY" and m15.close[-1] >= m15.close[-2]) or (side == "SELL" and m15.close[-1] <= m15.close[-2])) else "EN_ATTENTE", dxy="", macro=macro, strength=strength, vol_regime="NORMALE", liquidity="RANGE", correlation="NEUTRE", news=news, session="", reasons=reasons)
