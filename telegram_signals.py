"""
Trading 212 CFD Scanner — 15 min
- Real market data from Yahoo Finance chart endpoint
- No hard-coded institutional/dark-pool/options claims
- 15m trend + 1h trend, VWAP, ATR, volume, breakout, relative strength
- Long and short setups
- ATR/structure-based TP/SL
- Telegram alerts with cooldown/dedup
- Alerting only: does NOT place trades on Trading 212
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("t212-scanner")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
MIN_SCORE = int(os.getenv("MIN_SCORE", "75"))
MAX_ALERTS = int(os.getenv("MAX_ALERTS", "5"))
RISK_PCT = float(os.getenv("RISK_PCT", "1.0"))
ALERT_COOLDOWN_MIN = int(os.getenv("ALERT_COOLDOWN_MIN", "60"))
STATE_FILE = Path(os.getenv("STATE_FILE", "signal_state.json"))

YAHOO_BASE = "https://query1.finance.yahoo.com/v8/finance/chart"
SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": "Mozilla/5.0 T212Scanner/4.0",
        "Accept": "application/json",
    }
)

# Liquid/commonly available US equities. These are symbols only; all scores
# are computed from fresh market data on every scan.
SYMBOLS = [
    "NVDA","AMD","AVGO","QCOM","MU","MRVL","ARM","INTC","TSM","ASML",
    "MSFT","GOOGL","META","AMZN","AAPL","ORCL","CRM","ADBE","NOW","SNOW",
    "PLTR","AI","BBAI","SOUN","IONQ","CRWD","PANW","NET","DDOG","MDB",
    "COIN","HOOD","PYPL","SQ","MSTR","RIOT","MARA","SOFI","NU","AFRM",
    "TSLA","RIVN","LCID","UBER","LYFT","NIO","XPEV","GM","F","ABNB",
    "LMT","RTX","NOC","GD","BA","HWM","GE","CAT","DE","ETN",
    "LLY","NVO","MRNA","PFE","ABBV","JNJ","ISRG","UNH","AMGN","GILD",
    "JPM","BAC","GS","MS","V","MA","WMT","COST","HD","LOW",
    "XOM","CVX","COP","SLB","NFLX","DIS","PEP","KO",
]

BENCHMARK = "SPY"


@dataclass
class BarData:
    ts: list[int]
    open: list[float]
    high: list[float]
    low: list[float]
    close: list[float]
    volume: list[float]


@dataclass
class Signal:
    symbol: str
    side: str
    score: int
    price: float
    stop: float
    tp1: float
    tp2: float
    risk_per_share: float
    atr: float
    atr_pct: float
    volume_ratio: float
    trend15: str
    trend60: str
    reason: list[str]


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        log.warning("Etat illisible: %s", exc)
        return {}


def save_state(state: dict[str, Any]) -> None:
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def fetch_15m(symbol: str) -> BarData | None:
    params = {
        "range": "5d",
        "interval": "15m",
        "includePrePost": "false",
        "events": "div,splits",
    }
    try:
        r = SESSION.get(f"{YAHOO_BASE}/{symbol}", params=params, timeout=12)
        r.raise_for_status()
        result = r.json()["chart"]["result"]
        if not result:
            return None
        item = result[0]
        ts = item.get("timestamp", [])
        quote = item["indicators"]["quote"][0]
        rows = []
        for i, t in enumerate(ts):
            vals = (
                quote["open"][i],
                quote["high"][i],
                quote["low"][i],
                quote["close"][i],
                quote["volume"][i],
            )
            if all(v is not None for v in vals):
                rows.append((int(t), *map(float, vals)))
        if len(rows) < 120:
            return None
        return BarData(
            ts=[x[0] for x in rows],
            open=[x[1] for x in rows],
            high=[x[2] for x in rows],
            low=[x[3] for x in rows],
            close=[x[4] for x in rows],
            volume=[x[5] for x in rows],
        )
    except Exception as exc:
        log.warning("%s: market-data error: %s", symbol, exc)
        return None


def ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    k = 2 / (period + 1)
    out = [values[0]]
    for x in values[1:]:
        out.append(x * k + out[-1] * (1 - k))
    return out


def atr(data: BarData, period: int = 14) -> float:
    tr: list[float] = []
    prev = None
    for h, l, c in zip(data.high, data.low, data.close):
        if prev is None:
            tr.append(h - l)
        else:
            tr.append(max(h - l, abs(h - prev), abs(l - prev)))
        prev = c
    if len(tr) < period:
        return 0.0
    return sum(tr[-period:]) / period


def vwap_session(data: BarData) -> float:
    day_key = datetime.fromtimestamp(data.ts[-1], tz=timezone.utc).date()
    pvt = 0.0
    vol = 0.0
    for t, h, l, c, v in zip(data.ts, data.high, data.low, data.close, data.volume):
        dt = datetime.fromtimestamp(t, tz=timezone.utc)
        if dt.date() != day_key or v <= 0:
            continue
        pvt += ((h + l + c) / 3) * v
        vol += v
    return pvt / vol if vol else data.close[-1]


def resample_1h(data: BarData) -> BarData:
    n = len(data.close)
    groups = []
    start = n % 4
    for i in range(start, n - 3, 4):
        sl = slice(i, i + 4)
        groups.append(
            (
                data.ts[i],
                data.open[i],
                max(data.high[sl]),
                min(data.low[sl]),
                data.close[i + 3],
                sum(data.volume[sl]),
            )
        )
    return BarData(
        ts=[x[0] for x in groups],
        open=[x[1] for x in groups],
        high=[x[2] for x in groups],
        low=[x[3] for x in groups],
        close=[x[4] for x in groups],
        volume=[x[5] for x in groups],
    )


def trend_score(data: BarData) -> tuple[int, str]:
    e9 = ema(data.close, 9)
    e21 = ema(data.close, 21)
    if len(e21) < 5:
        return 0, "NEUTRAL"
    rising = e21[-1] > e21[-4]
    falling = e21[-1] < e21[-4]
    bullish = e9[-1] > e21[-1]
    bearish = e9[-1] < e21[-1]
    score = 0
    if bullish and rising:
        score = 2
    elif bullish:
        score = 1
    elif bearish and falling:
        score = -2
    elif bearish:
        score = -1
    return score, "BULLISH" if score > 0 else "BEARISH" if score < 0 else "NEUTRAL"


def build_signal(symbol: str, data15: BarData, market15: BarData | None) -> Signal | None:
    data60 = resample_1h(data15)
    if len(data60.close) < 30:
        return None

    price = data15.close[-1]
    a = atr(data15, 14)
    if a <= 0 or price <= 0:
        return None
    atr_pct = a / price * 100

    t15, t15_lbl = trend_score(data15)
    t60, t60_lbl = trend_score(data60)
    vwap = vwap_session(data15)

    vol_window = data15.volume[-21:-1]
    avg_vol = sum(vol_window) / len(vol_window) if vol_window else 0
    vol_ratio = data15.volume[-1] / avg_vol if avg_vol else 0

    prior_high = max(data15.high[-21:-1])
    prior_low = min(data15.low[-21:-1])

    e21 = ema(data15.close, 21)[-1]
    extension = abs(price - e21) / a if a else 99

    market_bias = 0
    if market15 is not None:
        mt, _ = trend_score(resample_1h(market15))
        market_bias = 1 if mt > 0 else -1 if mt < 0 else 0

    long_points = 0
    short_points = 0
    long_reason: list[str] = []
    short_reason: list[str] = []

    if t15 >= 2:
        long_points += 20
        long_reason.append("tendance 15m haussière")
    elif t15 > 0:
        long_points += 10
        long_reason.append("EMA 9/21 15m positive")

    if t60 >= 2:
        long_points += 20
        long_reason.append("tendance 1h haussière")
    elif t60 > 0:
        long_points += 10

    if price > vwap:
        long_points += 10
        long_reason.append("prix au-dessus VWAP")
    else:
        short_points += 10
        short_reason.append("prix sous VWAP")

    if price > prior_high:
        long_points += 20
        long_reason.append("cassure du plus haut 20 bougies")
    if price < prior_low:
        short_points += 20
        short_reason.append("cassure du plus bas 20 bougies")

    if vol_ratio >= 2.0:
        if price >= vwap:
            long_points += 10
            long_reason.append(f"volume {vol_ratio:.1f}x")
        else:
            short_points += 10
            short_reason.append(f"volume {vol_ratio:.1f}x")
    elif vol_ratio >= 1.5:
        if price >= vwap:
            long_points += 6
        else:
            short_points += 6

    if 0.5 <= atr_pct <= 5.0:
        long_points += 5
        short_points += 5
    elif atr_pct > 7.0:
        long_points -= 8
        short_points -= 8

    if extension > 3.5:
        long_points -= 8
        short_points -= 8

    if market_bias > 0:
        long_points += 5
        long_reason.append("marché US favorable")
    elif market_bias < 0:
        short_points += 5
        short_reason.append("marché US faible")

    side = "BUY" if long_points > short_points else "SELL"
    score = max(long_points, short_points)

    if score < MIN_SCORE:
        return None
    if side == "BUY" and not (t15 > 0 and t60 > 0):
        return None
    if side == "SELL" and not (t15 < 0 and t60 < 0):
        return None

    if side == "BUY":
        stop = min(price - 1.5 * a, min(data15.low[-8:]) - 0.1 * a)
        if stop <= 0 or stop >= price:
            return None
        r = price - stop
        tp1 = price + r
        tp2 = price + 2 * r
        reasons = long_reason
    else:
        stop = max(price + 1.5 * a, max(data15.high[-8:]) + 0.1 * a)
        if stop <= price:
            return None
        r = stop - price
        tp1 = price - r
        tp2 = price - 2 * r
        reasons = short_reason

    if r <= 0:
        return None

    return Signal(
        symbol=symbol,
        side=side,
        score=int(min(100, round(score))),
        price=price,
        stop=stop,
        tp1=tp1,
        tp2=tp2,
        risk_per_share=r,
        atr=a,
        atr_pct=atr_pct,
        volume_ratio=vol_ratio,
        trend15=t15_lbl,
        trend60=t60_lbl,
        reason=reasons,
    )


def is_market_open() -> bool:
    return datetime.now(timezone.utc).weekday() < 5


def telegram_send(text: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID manquants dans les secrets.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        r = SESSION.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "disable_web_page_preview": True,
            },
            timeout=12,
        )
        if r.status_code != 200:
            log.error("Telegram HTTP %s: %s", r.status_code, r.text[:250])
            return False
        return True
    except requests.RequestException as exc:
        log.error("Telegram error: %s", exc)
        return False


def format_signal(s: Signal) -> str:
    icon = "🟢" if s.side == "BUY" else "🔴"
    direction = "ACHAT" if s.side == "BUY" else "VENTE"
    reason = " • ".join(s.reason[:5]) or "confluence technique"
    return (
        f"{icon} {direction} — {s.symbol}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Score: {s.score}/100\n"
        f"Prix: {s.price:.2f}\n"
        f"SL: {s.stop:.2f}\n"
        f"TP1: {s.tp1:.2f} (1R)\n"
        f"TP2: {s.tp2:.2f} (2R)\n"
        f"ATR: {s.atr_pct:.2f}%\n"
        f"Volume: {s.volume_ratio:.1f}x\n"
        f"Trend 15m: {s.trend15}\n"
        f"Trend 1h: {s.trend60}\n"
        f"Confluence: {reason}\n"
        f"Risque indicatif: {RISK_PCT:.2f}% du capital\n"
        f"⚠️ Signal analytique uniquement — aucun ordre Trading 212 n'est exécuté."
    )


def main() -> int:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("Configuration Telegram absente. Utiliser uniquement GitHub Secrets.")
        return 2

    if not is_market_open():
        log.info("Week-end: scan ignoré.")
        return 0

    market = fetch_15m(BENCHMARK)
    state = load_state()
    now = time.time()
    signals: list[Signal] = []

    for i, symbol in enumerate(SYMBOLS, 1):
        log.info("[%d/%d] Scan %s", i, len(SYMBOLS), symbol)
        data = fetch_15m(symbol)
        if data is None:
            continue
        sig = build_signal(symbol, data, market)
        if sig:
            signals.append(sig)
        time.sleep(0.15)

    signals.sort(key=lambda s: s.score, reverse=True)
    sent = 0
    for sig in signals[:MAX_ALERTS]:
        key = f"{sig.symbol}:{sig.side}"
        previous = state.get(key, {})
        last_sent = float(previous.get("sent_at", 0))
        last_price = float(previous.get("price", 0))
        cooldown_ok = (now - last_sent) >= ALERT_COOLDOWN_MIN * 60
        moved_ok = last_price <= 0 or abs(sig.price - last_price) >= 0.5 * sig.atr

        if not cooldown_ok and not moved_ok:
            continue

        if telegram_send(format_signal(sig)):
            state[key] = {"sent_at": now, "price": sig.price, "score": sig.score}
            save_state(state)
            sent += 1

    log.info(
        "Scan termine: %d candidats, %d alertes envoyées, seuil=%d",
        len(signals),
        sent,
        MIN_SCORE,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
