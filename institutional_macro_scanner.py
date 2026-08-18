"""
Institutional + Macro overlay for the Trading 212 15m scanner.
Uses public market-data proxies only:
- Macro: VIX, UUP, TLT, GLD, USO, SPY, QQQ
- Institutional-pressure proxies: SPY/QQQ/sector ETF relative strength,
  relative volume, and breadth across the scanned universe.
This file does NOT claim to know live hedge-fund positions.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

import telegram_signals as base

log = logging.getLogger("t212-overlay")
MIN_SCORE = int(os.getenv("MIN_SCORE", "65"))
MAX_ALERTS = int(os.getenv("MAX_ALERTS", "5"))
ALERT_COOLDOWN_MIN = int(os.getenv("ALERT_COOLDOWN_MIN", "60"))

MACRO_SYMBOLS = ["SPY", "QQQ", "^VIX", "UUP", "TLT", "GLD", "USO"]
SECTOR_ETFS = {
    "TECH": "XLK", "SEMIS": "SMH", "FINANCE": "XLF", "ENERGY": "XLE",
    "HEALTH": "XLV", "INDUSTRIAL": "XLI", "CONSUMER": "XLY",
    "COMMUNICATION": "XLC", "UTILITIES": "XLU", "REAL_ESTATE": "XLRE", "MATERIALS": "XLB",
}
SECTOR_MAP = {
    "NVDA": "SMH", "AMD": "SMH", "AVGO": "SMH", "QCOM": "SMH", "MU": "SMH", "MRVL": "SMH", "ARM": "SMH", "INTC": "SMH", "TSM": "SMH", "ASML": "SMH",
    "MSFT": "XLK", "AAPL": "XLK", "ORCL": "XLK", "CRM": "XLK", "ADBE": "XLK", "NOW": "XLK", "SNOW": "XLK",
    "GOOGL": "XLC", "META": "XLC", "NFLX": "XLC", "DIS": "XLC",
    "AMZN": "XLY", "TSLA": "XLY", "RIVN": "XLY", "LCID": "XLY", "UBER": "XLY", "LYFT": "XLY", "ABNB": "XLY", "GM": "XLY", "F": "XLY",
    "JPM": "XLF", "BAC": "XLF", "GS": "XLF", "MS": "XLF", "V": "XLF", "MA": "XLF", "PYPL": "XLF", "SQ": "XLF", "HOOD": "XLF", "COIN": "XLF",
    "LLY": "XLV", "NVO": "XLV", "MRNA": "XLV", "PFE": "XLV", "ABBV": "XLV", "JNJ": "XLV", "ISRG": "XLV", "UNH": "XLV", "AMGN": "XLV", "GILD": "XLV",
    "XOM": "XLE", "CVX": "XLE", "COP": "XLE", "SLB": "XLE",
    "LMT": "XLI", "RTX": "XLI", "NOC": "XLI", "GD": "XLI", "BA": "XLI", "HWM": "XLI", "GE": "XLI", "CAT": "XLI", "DE": "XLI", "ETN": "XLI",
    "WMT": "XLP", "COST": "XLP", "HD": "XLY", "LOW": "XLY", "PEP": "XLP", "KO": "XLP",
}

@dataclass
class Overlay:
    macro_score: int
    macro_regime: str
    inst_score: int
    inst_label: str
    sector_score: int
    breadth: float
    reasons: list[str]

def _safe_fetch(symbol: str):
    try:
        return base.fetch_15m(symbol)
    except Exception as exc:
        log.warning("%s overlay data error: %s", symbol, exc)
        return None

def _change(data, bars: int = 4) -> float:
    if not data or len(data.close) <= bars or data.close[-bars - 1] == 0:
        return 0.0
    return (data.close[-1] / data.close[-bars - 1] - 1.0) * 100.0

def _volume_ratio(data, window: int = 20) -> float:
    if not data or len(data.volume) <= window + 1:
        return 1.0
    avg = sum(data.volume[-window - 1:-1]) / window
    return data.volume[-1] / avg if avg else 1.0

def build_overlay(data_by_symbol: dict[str, object], stock_signals: list[base.Signal]) -> tuple[Overlay, dict[str, tuple[float, float]]]:
    spy = data_by_symbol.get("SPY")
    qqq = data_by_symbol.get("QQQ")
    vix = data_by_symbol.get("^VIX")
    uup = data_by_symbol.get("UUP")
    tlt = data_by_symbol.get("TLT")
    spy_ret, qqq_ret, uup_ret, tlt_ret = _change(spy), _change(qqq), _change(uup), _change(tlt)
    macro = 0
    reasons: list[str] = []
    if spy_ret > 0.4:
        macro += 2; reasons.append("SPY momentum positif")
    elif spy_ret < -0.4:
        macro -= 2; reasons.append("SPY sous pression")
    if qqq_ret > 0.5:
        macro += 2; reasons.append("QQQ momentum positif")
    elif qqq_ret < -0.5:
        macro -= 2; reasons.append("QQQ sous pression")
    vix_ret = _change(vix)
    if vix is not None:
        if vix_ret <= -1.0:
            macro += 2; reasons.append("VIX en baisse")
        elif vix_ret >= 2.0:
            macro -= 3; reasons.append("VIX en hausse")
    if uup_ret < -0.25:
        macro += 1; reasons.append("dollar moins contraignant")
    elif uup_ret > 0.5:
        macro -= 1; reasons.append("dollar ferme")
    if tlt_ret > 0.5:
        macro += 1; reasons.append("TLT favorable au risk-on")
    elif tlt_ret < -0.8:
        macro -= 1; reasons.append("taux/rebond de rendement")
    macro = max(-8, min(8, macro))
    regime = "RISK-ON" if macro >= 4 else "RISK-OFF" if macro <= -4 else "MIXTE"

    inst = 0
    spy_v, qqq_v = _volume_ratio(spy), _volume_ratio(qqq)
    if spy_v >= 1.35:
        inst += 2 if spy_ret > 0 else -2; reasons.append("volume SPY anormal")
    if qqq_v >= 1.35:
        inst += 2 if qqq_ret > 0 else -2; reasons.append("volume QQQ anormal")
    up = sum(1 for s in stock_signals if s.side == "BUY")
    down = sum(1 for s in stock_signals if s.side == "SELL")
    breadth = up / max(1, up + down)
    if breadth >= 0.65:
        inst += 2; reasons.append("breadth haussière")
    elif breadth <= 0.35:
        inst -= 2; reasons.append("breadth baissière")
    inst = max(-8, min(8, inst))
    inst_label = "ACCUMULATION_PROXY" if inst >= 3 else "DISTRIBUTION_PROXY" if inst <= -3 else "NEUTRAL_PROXY"

    sector_perf: dict[str, tuple[float, float]] = {}
    sector_values = []
    for etf in SECTOR_ETFS.values():
        d = data_by_symbol.get(etf)
        if d:
            ret, vr = _change(d), _volume_ratio(d)
            sector_perf[etf] = (ret, vr)
            sector_values.append(ret)
    sector_avg = sum(sector_values) / len(sector_values) if sector_values else 0.0
    sector_score = 2 if sector_avg > 0.35 else -2 if sector_avg < -0.35 else 0
    if sector_score > 0: reasons.append("rotation sectorielle favorable")
    elif sector_score < 0: reasons.append("rotation sectorielle défavorable")
    return Overlay(macro, regime, inst, inst_label, sector_score, breadth, reasons[-8:]), sector_perf

def apply_overlay(sig: base.Signal, overlay: Overlay, sector_perf: dict[str, tuple[float, float]]) -> base.Signal | None:
    direction = 1 if sig.side == "BUY" else -1
    score = sig.score + direction * overlay.macro_score * 2 + direction * overlay.inst_score * 2 + direction * overlay.sector_score * 2
    reasons = list(sig.reason)
    etf = SECTOR_MAP.get(sig.symbol)
    if etf and etf in sector_perf:
        sector_ret, sector_vol = sector_perf[etf]
        stock_ret = _change(base.fetch_15m(sig.symbol))
        relative = stock_ret - sector_ret
        if (direction > 0 and relative > 0.25) or (direction < 0 and relative < -0.25):
            score += 3; reasons.append(f"force relative vs {etf}")
        if sector_vol >= 1.4:
            reasons.append(f"rotation {etf} volume {sector_vol:.1f}x")
    if overlay.macro_regime == "RISK-OFF" and sig.side == "BUY":
        score -= 4; reasons.append("macro risk-off")
    elif overlay.macro_regime == "RISK-ON" and sig.side == "SELL":
        score -= 4; reasons.append("macro risk-on")
    score = int(max(0, min(100, round(score))))
    if score < MIN_SCORE:
        return None
    sig.score, sig.reason = score, reasons[-6:]
    return sig

def format_overlay(overlay: Overlay) -> str:
    return f"Macro: {overlay.macro_regime} ({overlay.macro_score:+d})\nInstitutional proxy: {overlay.inst_label} ({overlay.inst_score:+d})\nBreadth: {overlay.breadth * 100:.0f}% BUY\n"

def main() -> int:
    if not base.TELEGRAM_BOT_TOKEN or not base.TELEGRAM_CHAT_ID:
        log.error("Configuration Telegram absente."); return 2
    if not base.is_market_open():
        log.info("Week-end: scan ignoré."); return 0
    symbols = dict.fromkeys(base.SYMBOLS + MACRO_SYMBOLS + list(SECTOR_ETFS.values()))
    data_by_symbol = {}
    for symbol in symbols:
        data_by_symbol[symbol] = _safe_fetch(symbol)
        time.sleep(0.08)
    market = data_by_symbol.get(base.BENCHMARK)
    signals: list[base.Signal] = []
    for symbol in base.SYMBOLS:
        data = data_by_symbol.get(symbol)
        if data is None: continue
        sig = base.build_signal(symbol, data, market)
        if sig: signals.append(sig)
    overlay, sector_perf = build_overlay(data_by_symbol, signals)
    enhanced = []
    for sig in signals:
        try:
            s = apply_overlay(sig, overlay, sector_perf)
            if s: enhanced.append(s)
        except Exception as exc:
            log.warning("overlay %s: %s", sig.symbol, exc)
    enhanced.sort(key=lambda s: s.score, reverse=True)
    state, now, sent = base.load_state(), time.time(), 0
    for sig in enhanced[:MAX_ALERTS]:
        key = f"{sig.symbol}:{sig.side}"
        prev = state.get(key, {})
        last_sent, last_price = float(prev.get("sent_at", 0)), float(prev.get("price", 0))
        cooldown_ok = (now - last_sent) >= ALERT_COOLDOWN_MIN * 60
        moved_ok = last_price <= 0 or abs(sig.price - last_price) >= 0.5 * sig.atr
        if not cooldown_ok and not moved_ok: continue
        if base.telegram_send(base.format_signal(sig) + "\n\n" + format_overlay(overlay)):
            state[key] = {"sent_at": now, "price": sig.price, "score": sig.score}
            base.save_state(state); sent += 1
    log.info("Overlay scan: %d candidats -> %d alertes | %s | %s", len(signals), sent, overlay.macro_regime, overlay.inst_label)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
