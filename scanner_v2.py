from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import telegram_signals as base

try:
    import institutional_macro_scanner as macro
except Exception as exc:
    raise RuntimeError(f"Macro engine import failed: {exc}") from exc

log = logging.getLogger("t212-scanner-v2")
MIN_CANDIDATE = int(os.getenv("MIN_SCORE", "45"))
FINAL_MIN = int(os.getenv("FINAL_MIN_SCORE", "60"))
WATCH_MIN = int(os.getenv("WATCH_MIN_SCORE", "52"))
MAX_ALERTS = int(os.getenv("MAX_ALERTS", "5"))
WATCH_ALERTS = int(os.getenv("WATCH_ALERTS", "3"))
COOLDOWN = int(os.getenv("ALERT_COOLDOWN_MIN", "60"))
WATCH_COOLDOWN = int(os.getenv("WATCH_COOLDOWN_MIN", "180"))

@dataclass
class Candidate:
    signal: base.Signal
    mode: str


def market_open_us() -> bool:
    now = datetime.now(ZoneInfo("America/New_York"))
    return now.weekday() < 5 and (now.hour > 9 or (now.hour == 9 and now.minute >= 30)) and (now.hour < 16)


def fetch_all(symbols: list[str]) -> dict[str, base.BarData | None]:
    out: dict[str, base.BarData | None] = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(base.fetch_15m, s): s for s in symbols}
        for f in as_completed(futures):
            s = futures[f]
            try:
                out[s] = f.result()
            except Exception as exc:
                log.warning("fetch %s: %s", s, exc)
                out[s] = None
    return out


def aligned_1h(data: base.BarData) -> base.BarData:
    buckets: dict[int, list[int]] = {}
    for i, ts in enumerate(data.ts):
        buckets.setdefault(ts // 3600, []).append(i)
    groups = []
    for _, idx in sorted(buckets.items()):
        if len(idx) < 3:
            continue
        groups.append((idx[0], idx[-1], idx))
    return base.BarData(
        ts=[data.ts[g[0]] for g in groups],
        open=[data.open[g[0]] for g in groups],
        high=[max(data.high[i] for i in g[2]) for g in groups],
        low=[min(data.low[i] for i in g[2]) for g in groups],
        close=[data.close[g[1]] for g in groups],
        volume=[sum(data.volume[i] for i in g[2]) for g in groups],
    )


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def prebreakout(symbol: str, d: base.BarData, spy: base.BarData | None) -> Candidate | None:
    if len(d.close) < 60:
        return None
    price = d.close[-1]
    atr20 = base.atr(d, 20)
    atr6 = base.atr(d, 6)
    if atr20 <= 0 or price <= 0:
        return None
    ema21 = base.ema(d.close, 21)
    ema9 = base.ema(d.close, 9)
    h1 = aligned_1h(d)
    t15, t15lbl = base.trend_score(d)
    t60, t60lbl = base.trend_score(h1) if len(h1.close) >= 25 else (0, "NEUTRAL")
    prior_high = max(d.high[-21:-1])
    prior_low = min(d.low[-21:-1])
    dist_up = (prior_high - price) / atr20
    dist_down = (price - prior_low) / atr20
    ranges = [max(0.0, h - l) for h, l in zip(d.high, d.low)]
    compression = mean(ranges[-6:]) / max(mean(ranges[-24:-6]), 1e-9)
    vol_avg = mean(d.volume[-21:-1])
    vol3 = mean(d.volume[-3:])
    vol_ratio = d.volume[-1] / vol_avg if vol_avg else 0.0
    accumulation = compression <= 0.78 and vol3 <= vol_avg * 0.95 and d.volume[-1] >= vol_avg * 1.10
    higher_lows = d.low[-1] > d.low[-3] > d.low[-5]
    lower_highs = d.high[-1] < d.high[-3] < d.high[-5]
    rs = 0.0
    if spy is not None and len(spy.close) > 8:
        rs = (d.close[-1] / d.close[-9] - 1) * 100 - (spy.close[-1] / spy.close[-9] - 1) * 100
    long_score = 0
    short_score = 0
    long_r: list[str] = ["PRE-BREAKOUT"]
    short_r: list[str] = ["PRE-BREAKOUT"]
    if price > ema21[-1] and ema21[-1] > ema21[-5]:
        long_score += 15; long_r.append("EMA21 ascendante")
    if t15 > 0 or t60 > 0:
        long_score += 10; long_r.append("tendance alignée")
    if 0 <= dist_up <= 0.85:
        long_score += 20; long_r.append(f"résistance à {dist_up:.2f} ATR")
    if accumulation:
        long_score += 15; long_r.append("compression + reprise volume")
    elif compression <= 0.82:
        long_score += 8; long_r.append("compression")
    if higher_lows:
        long_score += 10; long_r.append("creux ascendants")
    if rs > 0.20:
        long_score += 10; long_r.append("force relative")
    if 0.5 <= atr20 / price * 100 <= 5:
        long_score += 5
    if price < ema21[-1] and ema21[-1] < ema21[-5]:
        short_score += 15; short_r.append("EMA21 descendante")
    if t15 < 0 or t60 < 0:
        short_score += 10; short_r.append("tendance baissière")
    if 0 <= dist_down <= 0.85:
        short_score += 20; short_r.append(f"support à {dist_down:.2f} ATR")
    if compression <= 0.78 and vol3 <= vol_avg * 0.95 and d.volume[-1] >= vol_avg * 1.10:
        short_score += 12; short_r.append("compression + reprise volume")
    if lower_highs:
        short_score += 10; short_r.append("sommets descendants")
    if rs < -0.20:
        short_score += 10; short_r.append("faiblesse relative")
    side = "BUY" if long_score >= short_score else "SELL"
    score = max(long_score, short_score)
    if score < MIN_CANDIDATE:
        return None
    a = atr20
    if side == "BUY":
        stop = min(price - 1.35 * a, min(d.low[-8:]) - 0.10 * a)
        if not (0 < stop < price): return None
        r = price - stop; tp1 = price + r; tp2 = price + 2 * r
        reasons = long_r
    else:
        stop = max(price + 1.35 * a, max(d.high[-8:]) + 0.10 * a)
        if stop <= price: return None
        r = stop - price; tp1 = price - r; tp2 = price - 2 * r
        reasons = short_r
    return Candidate(base.Signal(symbol, side, min(100, score), price, stop, tp1, tp2, r, a, a/price*100, vol_ratio, t15lbl, t60lbl, reasons), "PRE-BREAKOUT")


def breadth(data: dict[str, base.BarData | None]) -> float:
    good = total = 0
    for s in base.SYMBOLS:
        d = data.get(s)
        if not d or len(d.close) < 30: continue
        e = base.ema(d.close, 21)
        total += 1
        if d.close[-1] > e[-1] and e[-1] > e[-5]: good += 1
    return good / max(total, 1)


def macro_overlay(data: dict[str, base.BarData | None], candidates: list[Candidate]) -> tuple[int, str, int, str, int, float, list[str]]:
    spy, qqq, vix, uup, tlt = (data.get(x) for x in ["SPY", "QQQ", "^VIX", "UUP", "TLT"])
    ret = lambda x, n=4: ((x.close[-1]/x.close[-n-1]-1)*100) if x and len(x.close)>n else 0.0
    m = 0; reasons=[]
    for value, pos, neg, label in [(ret(spy),0.4,-0.4,"SPY"),(ret(qqq),0.5,-0.5,"QQQ")]:
        if value > pos: m += 2; reasons.append(f"{label} positif")
        elif value < neg: m -= 2; reasons.append(f"{label} faible")
    vr = ret(vix)
    if vix:
        if vr <= -1: m += 2; reasons.append("VIX baisse")
        elif vr >= 2: m -= 3; reasons.append("VIX hausse")
    ur = ret(uup)
    if ur < -0.25: m += 1; reasons.append("dollar moins contraignant")
    elif ur > 0.5: m -= 1; reasons.append("dollar ferme")
    tr = ret(tlt)
    if tr > 0.5: m += 1
    elif tr < -0.8: m -= 1
    m = max(-8,min(8,m)); regime = "RISK-ON" if m>=4 else "RISK-OFF" if m<=-4 else "MIXTE"
    inst=0; sv = (data.get("SPY")); qv=(data.get("QQQ"))
    for x, label in [(sv,"SPY"),(qv,"QQQ")]:
        if x and len(x.volume)>21:
            vrx=x.volume[-1]/max(mean(x.volume[-21:-1]),1)
            if vrx>=1.35: inst += 2 if ret(x)>0 else -2; reasons.append(f"volume {label} anormal")
    br = breadth(data)
    if br >= .62: inst += 2; reasons.append("breadth forte")
    elif br <= .38: inst -= 2; reasons.append("breadth faible")
    inst=max(-8,min(8,inst)); ilabel="ACCUMULATION_PROXY" if inst>=3 else "DISTRIBUTION_PROXY" if inst<=-3 else "NEUTRAL_PROXY"
    sector_vals=[]
    for etf in macro.SECTOR_ETFS.values():
        d=data.get(etf)
        if d: sector_vals.append(ret(d))
    sec=2 if mean(sector_vals)>0.35 else -2 if mean(sector_vals)<-0.35 else 0
    if sec>0: reasons.append("rotation sectorielle favorable")
    if sec<0: reasons.append("rotation sectorielle défavorable")
    return m,regime,inst,ilabel,sec,br,reasons[-8:]


def adjust(c: Candidate, ov: tuple[int,str,int,str,int,float,list[str]], data: dict[str, base.BarData | None]) -> Candidate | None:
    m, regime, inst, ilabel, sec, br, reasons = ov
    direction = 1 if c.signal.side == "BUY" else -1
    score = c.signal.score + direction*(2*m + 2*inst + 2*sec)
    etf = macro.SECTOR_MAP.get(c.signal.symbol)
    if etf and data.get(etf) and data.get(c.signal.symbol):
        rel = ((data[c.signal.symbol].close[-1]/data[c.signal.symbol].close[-9])-1)*100 - ((data[etf].close[-1]/data[etf].close[-9])-1)*100
        if direction*rel > 0.25: score += 3; c.signal.reason.append(f"force relative vs {etf}")
    if regime == "RISK-OFF" and c.signal.side == "BUY": score -= 4; c.signal.reason.append("macro risk-off")
    if regime == "RISK-ON" and c.signal.side == "SELL": score -= 4; c.signal.reason.append("macro risk-on")
    score = int(max(0,min(100,round(score))))
    c.signal.score = score
    c.signal.reason = c.signal.reason[-6:]
    if score < FINAL_MIN and c.mode != "PRE-BREAKOUT": return None
    return c


def text(c: Candidate, ov) -> str:
    s=c.signal; icon="🟢" if s.side=="BUY" else "🔴"
    mode="🔥 PRE-BREAKOUT / ACCUMULATION" if c.mode=="PRE-BREAKOUT" else "SIGNAL CONFIRMÉ"
    return (f"{icon} {mode} — {s.side} {s.symbol}\n━━━━━━━━━━━━━━━━━━\nScore: {s.score}/100\nPrix: {s.price:.2f}\nSL: {s.stop:.2f}\nTP1: {s.tp1:.2f}\nTP2: {s.tp2:.2f}\nATR: {s.atr_pct:.2f}%\nVolume: {s.volume_ratio:.1f}x\nTrend 15m: {s.trend15}\nTrend 1h: {s.trend60}\nConfluence: {' • '.join(s.reason)}\nMacro: {ov[1]} ({ov[0]:+d})\nInstitutional proxy: {ov[3]} ({ov[2]:+d})\nBreadth: {ov[5]*100:.0f}%\n⚠️ Analytique uniquement — aucun ordre Trading 212 n'est exécuté.")


def send_once(c: Candidate, state: dict, now: float, cooldown: int) -> bool:
    key=f"{c.mode}:{c.signal.symbol}:{c.signal.side}"
    prev=state.get(key,{})
    last=float(prev.get("sent_at",0))
    if now-last < cooldown: return False
    if base.telegram_send(text(c, CURRENT_OVERLAY)):
        state[key]={"sent_at":now,"price":c.signal.price,"score":c.signal.score}
        return True
    return False

CURRENT_OVERLAY=None

def main() -> int:
    if not base.TELEGRAM_BOT_TOKEN or not base.TELEGRAM_CHAT_ID: return 2
    if not market_open_us():
        base.telegram_send("ℹ️ Trading 212 Scanner: marché US fermé — aucun signal analysé.")
        return 0
    symbols=list(dict.fromkeys(base.SYMBOLS+macro.MACRO_SYMBOLS+list(macro.SECTOR_ETFS.values())))
    data=fetch_all(symbols); spy=data.get("SPY")
    candidates=[]
    base.MIN_SCORE=MIN_CANDIDATE
    for symbol in base.SYMBOLS:
        d=data.get(symbol)
        if not d: continue
        sig=base.build_signal(symbol,d,spy)
        if sig: candidates.append(Candidate(sig,"CONFIRMÉ"))
        pb=prebreakout(symbol,d,spy)
        if pb: candidates.append(pb)
    best={}
    for c in candidates:
        k=(c.signal.symbol,c.signal.side)
        if k not in best or c.signal.score>best[k].signal.score: best[k]=c
    candidates=list(best.values())
    ov=macro_overlay(data,candidates)
    global CURRENT_OVERLAY
    CURRENT_OVERLAY=ov
    enhanced=[x for x in (adjust(c,ov,data) for c in candidates) if x]
    enhanced.sort(key=lambda c:c.signal.score,reverse=True)
    state=base.load_state(); now=time.time(); sent=0
    confirmed=[c for c in enhanced if c.mode=="CONFIRMÉ" and c.signal.score>=FINAL_MIN]
    watches=[c for c in enhanced if c.mode=="PRE-BREAKOUT" and c.signal.score>=WATCH_MIN]
    for c in confirmed[:MAX_ALERTS]:
        if send_once(c,state,now,COOLDOWN): sent += 1
    if not confirmed:
        for c in watches[:WATCH_ALERTS]:
            if send_once(c,state,now,WATCH_COOLDOWN): sent += 1
    base.save_state(state)
    base.telegram_send(f"📊 Scan V2: {len(data)}/{len(symbols)} données | candidats {len(candidates)} | confirmés {len(confirmed)} | pré-breakout {len(watches)} | macro {ov[1]}")
    log.info("V2 complete: data=%d/%d candidates=%d confirmed=%d watches=%d sent=%d",sum(x is not None for x in data.values()),len(data),len(candidates),len(confirmed),len(watches),sent)
    return 0

if __name__=="__main__": raise SystemExit(main())
