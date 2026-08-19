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
import forex_symbols as fxsym
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

# Per-scan rejection diagnostics. Filled by build_signal so the operator can
# tell "no opportunity" apart from "broken pipeline".
# Short-horizon currency strength, computed in main() from the M15 frames and
# read by build_signal. Kept as module state so the build_signal signature — and
# every wrapper built on it (v4 taux, v7, backtest) — stays unchanged.
INTRADAY_STRENGTH: dict[str, float] = {}

# Intraday risk regime, derived from the FX market itself. Equity/VIX proxies
# are unusable here: they are closed during the Asian and early London
# sessions, exactly when the scanner keeps trading.
HAVEN_CCY = tuple(os.getenv("FOREX_HAVEN_CCY", "JPY,CHF").split(","))
RISK_CCY = tuple(os.getenv("FOREX_RISK_CCY", "AUD,NZD,CAD").split(","))
REGIME_MIN = float(os.getenv("FOREX_REGIME_MIN", "0.10"))
INTRADAY_REGIME: dict[str, object] = {}

DIAG: dict[str, int] = {}
DIAG_DETAIL: dict[str, str] = {}


def diag_reset() -> None:
    DIAG.clear()
    DIAG_DETAIL.clear()


def diag_note(symbol: str, reason: str) -> None:
    DIAG[reason] = DIAG.get(reason, 0) + 1
    DIAG_DETAIL[str(symbol)] = reason

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
    strength_intraday: str = ""
    regime_intraday: str = ""


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


def _true_ranges(d: Bars) -> list[float]:
    tr, prev = [], d.close[0]
    for h, l, c in zip(d.high[1:], d.low[1:], d.close[1:]):
        tr.append(max(h - l, abs(h - prev), abs(l - prev)))
        prev = c
    return tr


def volatility_regime(d: Bars | None, p: int = 14) -> str:
    """ATR now vs the average ATR of the last 30 bars.

    Mathematically identical to the previous implementation but O(n) instead of
    O(n^2) — the old version rebuilt a full Bars slice and recomputed the whole
    true-range series for every bar, which dominated the scan runtime.
    """
    if not d or len(d.close) < 60:
        return "INCONNU"
    tr = _true_ranges(d)
    if len(tr) < p + 1:
        return "INCONNU"
    prefix = [0.0]
    for value in tr:
        prefix.append(prefix[-1] + value)

    def window(i: int) -> float:
        # mean(tr[i - p - 1 : i - 1]) == atr(Bars sliced to i bars)
        lo, hi = i - p - 1, i - 1
        return (prefix[hi] - prefix[lo]) / p

    n = len(d.close)
    now = window(n)
    if now <= 0:
        return "INCONNU"
    hist = [window(i) for i in range(max(30, n - 30), n)]
    med = mean(hist) if hist else now
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


def intraday_strength(m15_by_symbol: dict[str, Bars | None]) -> dict[str, float]:
    """Currency strength over the last ~4 h and ~24 h of M15 data.

    ``currency_strength`` averages daily returns over 5/20/60 sessions, so it
    describes the past few weeks. An M15 entry lives for minutes to hours, and
    the two can point in opposite directions — that gap is a real risk, not a
    detail, so it is measured explicitly instead of being assumed away.
    """
    horizons = (16, 96)  # ≈ 4 h and ≈ 24 h of 15-minute bars
    out: dict[str, list[float]] = {}
    for symbol, bars in m15_by_symbol.items():
        key = pair_key(symbol)
        if key not in PAIRS or not bars:
            continue
        a, b, _ = PAIRS[key]
        values = [ret(bars, n) for n in horizons if len(bars.close) > n]
        if not values:
            continue
        r = mean(values)
        out.setdefault(a, []).append(r)
        out.setdefault(b, []).append(-r)
    return {k: mean(v) for k, v in out.items() if v}


def intraday_risk_regime(strength: dict[str, float]) -> tuple[str, float]:
    """Are safe havens being bought or sold *right now*?

    ``macro_regime`` reads DXY/VIX/SPY/US10Y on daily bars, so it describes the
    last few weeks and is blind to an intraday risk shift. Measuring haven
    currencies against commodity currencies uses the FX market itself, which
    trades 24/5.

    Returns ``(label, score)``; a positive score means havens are outperforming,
    i.e. risk-off.
    """
    havens = [strength[c] for c in HAVEN_CCY if c in strength]
    risky = [strength[c] for c in RISK_CCY if c in strength]
    if not havens or not risky:
        return "INCONNU", 0.0
    score = mean(havens) - mean(risky)
    if score >= REGIME_MIN:
        return "RISK-OFF INTRADAY", score
    if score <= -REGIME_MIN:
        return "RISK-ON INTRADAY", score
    return "NEUTRE INTRADAY", score


def haven_leg(pair: object, side: str) -> tuple[str | None, str | None]:
    """Return ``(haven_being_sold, haven_being_bought)`` for this trade."""
    parts = fxsym.split(pair)
    if not parts:
        return None, None
    base, quote = parts
    long_base = str(side).upper() == "BUY"
    sold = bought = None
    if base in HAVEN_CCY:
        (bought := base) if long_base else (sold := base)
    if quote in HAVEN_CCY:
        (sold := quote) if long_base else (bought := quote)
    return sold, bought


def liquidity_state(m15: Bars, d1: Bars) -> tuple[str, int]:
    if not m15 or not d1 or len(m15.close) < 21 or len(d1.high) < 2:
        return "INCONNU", 0
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


def correlation_bias(pair: str, frames: dict[str, Bars | None], side: str | None = None) -> tuple[str, int]:
    """Correlation proxy for *pair*.

    The returned score is expressed in "favours BUY" terms. The label is
    resolved against *side* when known: a negative score is a *confirmation*
    for a SELL, not a contradiction. The previous side-agnostic label caused
    valid short setups to be rejected as "CONTRE".
    """
    key = pair_key(pair)
    if key not in PAIRS:
        return "NEUTRE", 0
    a, b, _ = PAIRS[key]
    dxy_r = ret(frames.get(DXY), 20)
    us10_r = ret(frames.get(US10Y), 20)
    score = 0
    if "USD" in (a, b):
        usd_long = dxy_r > 1.5
        if a == "USD" and usd_long: score += 2
        if b == "USD" and usd_long: score -= 2
        if a == "USD" and dxy_r < -1.5: score -= 2
        if b == "USD" and dxy_r < -1.5: score += 2
    if "JPY" in (a, b):
        if us10_r > 2: score += 1 if a == "USD" else 0
        if us10_r < -2: score -= 1 if a == "USD" else 0
    if side is None:
        aligned = score
    else:
        aligned = score if str(side).upper() == "BUY" else -score
    label = "CONFIRMÉ" if aligned > 0 else "CONTRE" if aligned < 0 else "NEUTRE"
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


def pair_key(pair: object) -> str:
    """Resolve any spelling of a pair to the key used by ``PAIRS``.

    Accepts ``EUR/USD``, ``EURUSD``, ``EURUSD=X`` (and separator/case variants)
    via the centralised :mod:`forex_symbols` normaliser. Returns the input
    unchanged when it cannot be resolved, so callers keep their own guard.
    """
    resolved = fxsym.resolve_key(pair, PAIRS)
    if resolved is not None:
        return resolved
    canonical = fxsym.canonical(pair)
    return canonical if canonical is not None else str(pair)


def event_risk(pair: object, events: list[dict]) -> tuple[str, bool]:
    key = pair_key(pair)
    if key in PAIRS:
        base_ccy, quote_ccy = PAIRS[key][0], PAIRS[key][1]
    else:
        parts = fxsym.split(pair)
        if not parts:
            LOG.warning("Unknown forex pair in event_risk: %r", pair)
            return "PAIRE INCONNUE — RISQUE NEWS NON EVALUE", False
        base_ccy, quote_ccy = parts
    now = time.time()
    for ev in events:
        if not isinstance(ev, dict):
            continue
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


def build_signal(pair: object, frames: dict[str, Bars | None], strength: dict[str, float], macro: str, macro_reason: str, news: str, news_block: bool) -> FxSignal | None:
    # Normalise first: no code path may depend on being handed a technical key.
    pair = pair_key(pair)
    if pair not in PAIRS:
        LOG.warning("Unknown forex pair in build_signal: %r", pair)
        diag_note(pair, "paire_inconnue")
        return None
    a, b, label = PAIRS[pair]
    d1, h4, h1, m15 = frames.get("d1"), frames.get("h4"), frames.get("h1"), frames.get("m15")
    if not all((d1, h4, h1, m15)):
        missing = [k for k in ("d1", "h4", "h1", "m15") if not frames.get(k)]
        diag_note(pair, "donnees_insuffisantes:" + "+".join(missing))
        return None
    td, th4, th1 = trend(d1), trend(h4), trend(h1)
    strong = strength.get(a, 0) - strength.get(b, 0)
    intra = INTRADAY_STRENGTH.get(a, 0.0) - INTRADAY_STRENGTH.get(b, 0.0)
    regime_label = str(INTRADAY_REGIME.get("label", "") or "")
    regime_score = float(INTRADAY_REGIME.get("score", 0.0) or 0.0)
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
    if long == short:
        # No directional edge at all: previously this silently became a SELL.
        diag_note(pair, "aucune_direction")
        return None
    side = "BUY" if long > short else "SELL"
    corr_label, _ = correlation_bias(pair, frames, side)
    raw = max(long, short)
    # The correlation bonus must only reward the side the correlation supports.
    aligned_corr = corr_score if side == "BUY" else -corr_score
    score = raw + max(0, aligned_corr)
    if macro == "RISK-ON" and a in ("AUD", "NZD", "CAD"): score += 3 if side == "BUY" else 0
    if macro == "RISK-OFF" and b in ("JPY", "CHF"): score += 3 if side == "SELL" else 0
    if news_block:
        diag_note(pair, "news_bloquante")
        return None
    if score < SETUP_MIN:
        diag_note(pair, "score_sous_seuil_setup")
        return None
    sess = session_name()
    if sess == "HORS_SESSION":
        diag_note(pair, "hors_session")
        return None
    vol = volatility_regime(m15)
    if vol == "EXPLOSIVE": score -= 3
    if vol == "FAIBLE": score -= 2
    p = m15.close[-1]; a15 = atr(m15); a1 = atr(h1)
    if a15 <= 0 or a1 <= 0:
        diag_note(pair, "atr_invalide")
        return None
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
    # Real reward/risk measured on the actual stop distance. The hard-coded 1.7
    # was wrong whenever the ATR floor widened `risk` beyond |entry - SL|.
    stop_distance = abs(p - sl)
    rr = round(abs(tp1 - p) / stop_distance, 2) if stop_distance > 0 else 0.0
    if rr <= 0:
        diag_note(pair, "rr_invalide")
        return None
    final_score = max(0, min(100, int(score + (liq_score if trigger else 0))))
    diag_note(pair, "retenu_" + state.lower())
    reasons = lr if side == "BUY" else sr
    return FxSignal(label, pair, side, state, final_score, p, sl, tp1, tp2, rr,
                    "BULLISH" if td > 0 else "BEARISH" if td < 0 else "MIXTE",
                    "BULLISH" if th4 > 0 else "BEARISH" if th4 < 0 else "MIXTE",
                    "BULLISH" if th1 > 0 else "BEARISH" if th1 < 0 else "MIXTE",
                    "CONFIRME" if trigger else "EN_ATTENTE",
                    dxy, macro, f"{a} {strong:+.1f} vs {b}", vol, sess, liq_label, corr_label, news, reasons,
                    f"{a} {intra:+.2f}% vs {b} (4-24h)" if INTRADAY_STRENGTH else "",
                    f"{regime_label} ({regime_score:+.2f})" if regime_label else "")


def price_fmt(pair: str, value: float) -> str:
    """JPY crosses quote in 0.01 units; everything else in 0.0001."""
    parts = fxsym.split(pair)
    quote = parts[1] if parts else ""
    return f"{value:.3f}" if quote == "JPY" else f"{value:.5f}"


def format_signal(s: FxSignal) -> str:
    icon = "🟢" if s.side == "BUY" else "🔴"
    action = "ACHAT" if s.side == "BUY" else "VENTE"
    medal = str(getattr(s, "medal_label", "") or "")
    header = f"{icon} {medal} — SIGNAL FOREX {s.state} — {s.pair}" if medal else \
             f"{icon} SIGNAL FOREX {s.state} — {s.pair}"

    lines = [
        header,
        "━━━━━━━━━━━━━━━━━━",
        "STRATÉGIE : D1 + H4 + H1 + M15",
        f"Direction : {action}",
        f"Score : {s.score}/100",
    ]
    quality_score = getattr(s, "quality_score", None)
    if quality_score is not None:
        lines.append(f"Qualité globale : {quality_score}/100")
    lines += [
        f"Entrée : {price_fmt(s.pair, s.price)}",
        f"SL : {price_fmt(s.pair, s.sl)}",
        f"TP1 : {price_fmt(s.pair, s.tp1)}",
        f"TP2 : {price_fmt(s.pair, s.tp2)}",
        f"R:R TP1 : 1:{s.rr:.2f}",
        f"D1 : {s.d1}",
        f"H4 : {s.h4}",
        f"H1 : {s.h1}",
        f"M15 : {s.m15}",
        f"DXY : {s.dxy}",
        f"Force multi-horizon : {s.strength}",
    ]
    if getattr(s, "strength_intraday", ""):
        lines.append(f"Force intraday : {s.strength_intraday}")
    if getattr(s, "regime_intraday", ""):
        lines.append(f"Régime intraday : {s.regime_intraday}")
    lines += [
        f"Volatilité : {s.vol_regime}",
        f"Liquidité : {s.liquidity}",
        f"Corrélation : {s.correlation}",
        f"Macro : {s.macro}",
        f"Session : {s.session}",
        f"News : {s.news}",
    ]
    coherence_label = getattr(s, "coherence_label", "")
    if coherence_label:
        lines.append(f"Cohérence multi-facteurs : {coherence_label}")

    verdict = str(getattr(s, "ai_verdict", "") or "")
    if verdict:
        if verdict == "INDISPONIBLE":
            lines.append("🤖 IA Cloudflare Qwen3 : INDISPONIBLE — signal validé par le moteur quantitatif seul")
        else:
            lines.append(f"🤖 IA Cloudflare Qwen3 : {verdict} ({getattr(s, 'ai_confidence', 0)}%)")
        reason = str(getattr(s, "ai_reason", "") or "")
        if reason and verdict != "INDISPONIBLE":
            lines.append(f"Motif IA : {reason}")

    if s.reasons:
        lines.append("Confluence :")
        lines += [f"• {r}" for r in s.reasons[:8]]
    lines.append("⚠️ Analyse uniquement — aucun ordre Forex n'est exécuté.")
    return "\n".join(lines)


LAST_RUN: dict = {}
SUPPRESS_SUMMARY = False
STATE_TTL_DAYS = float(os.getenv("FOREX_STATE_TTL_DAYS", "14"))


def rank_candidates(candidates: list["FxSignal"]) -> list["FxSignal"]:
    """Default ranking: raw score. Overridden downstream by the quality rank."""
    return sorted(candidates, key=lambda x: x.score, reverse=True)


def prune_state(state: dict) -> dict:
    """Drop cooldown entries far older than any cooldown window."""
    cutoff = time.time() - STATE_TTL_DAYS * 86400
    out = {}
    for key, value in state.items():
        if not key.startswith("FXV3:"):
            out[key] = value
            continue
        try:
            if float(value.get("sent_at", 0)) >= cutoff:
                out[key] = value
        except (AttributeError, TypeError, ValueError):
            continue
    return out


def main() -> int:
    diag_reset()
    LAST_RUN.clear()
    if not base.TELEGRAM_BOT_TOKEN or not base.TELEGRAM_CHAT_ID:
        LOG.error("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID manquants.")
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
    INTRADAY_STRENGTH.clear()
    INTRADAY_STRENGTH.update(intraday_strength({s: data[s].get("m15") for s in pairs}))
    _regime_label, _regime_score = intraday_risk_regime(INTRADAY_STRENGTH)
    INTRADAY_REGIME.clear()
    INTRADAY_REGIME.update({"label": _regime_label, "score": _regime_score})
    LOG.info("Régime intraday: %s (%+.3f)", _regime_label, _regime_score)
    macro, macro_reason = macro_regime(market)
    events = calendar_events()
    candidates = []
    setup_count = entry_count = blocked_news = 0
    for s in pairs:
        # Always index with the technical key, never a display label.
        news, blocked = event_risk(s, events)
        if blocked: blocked_news += 1
        sig = build_signal(s, data[s], strength, macro, macro_reason, news, blocked)
        if sig:
            candidates.append(sig)
            setup_count += 1
            entry_count += int(sig.state == "ENTREE")
    candidates = rank_candidates(candidates)
    state = base.load_state(); now = time.time(); sent = 0; cooldown_skipped = 0
    for sig in candidates[:MAX_ALERTS]:
        key = f"FXV3:{sig.pair}:{sig.side}:{sig.state}"
        if now - float(state.get(key, {}).get("sent_at", 0)) < COOLDOWN * 60:
            cooldown_skipped += 1
            diag_note(sig.symbol, "cooldown")
            continue
        if base.telegram_send(format_signal(sig)):
            state[key] = {"sent_at": now, "price": sig.price, "score": sig.score}
            sent += 1
        else:
            diag_note(sig.symbol, "echec_envoi_telegram")
    base.save_state(prune_state(state))
    LAST_RUN.update({
        "pairs": len(pairs),
        "setups": setup_count,
        "entries": entry_count,
        "sent": sent,
        "cooldown_skipped": cooldown_skipped,
        "blocked_news": blocked_news,
        "macro": macro,
        "macro_reason": macro_reason,
        "calendar_events": len(events),
        "candidates": candidates,
        "diag": dict(DIAG),
        "session": session_name(),
    })
    if not SUPPRESS_SUMMARY:
        base.telegram_send(
            f"💱 Scan FOREX v3 D1+H4+H1+M15: setups {setup_count} | entrées {entry_count} | envoyés {sent} | news bloqués {blocked_news} | Macro {macro}"
        )
    LOG.info(
        "Forex v3: setups=%d entries=%d sent=%d cooldown=%d news_bloques=%d",
        setup_count, entry_count, sent, cooldown_skipped, blocked_news,
    )
    LOG.info("Forex v3 diagnostic: %s", json.dumps(DIAG, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
