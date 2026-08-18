from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from statistics import median
from zoneinfo import ZoneInfo

import telegram_signals as base
import institutional_macro_scanner as macro

log = logging.getLogger("t212-scanner-v2")
CANDIDATE_MIN = int(os.getenv("MIN_SCORE", "42"))
FINAL_MIN = int(os.getenv("FINAL_MIN_SCORE", "58"))
WATCH_MIN = int(os.getenv("WATCH_MIN_SCORE", "45"))
MAX_ALERTS = int(os.getenv("MAX_ALERTS", "5"))
WATCH_ALERTS = int(os.getenv("WATCH_ALERTS", "3"))
COOLDOWN = int(os.getenv("ALERT_COOLDOWN_MIN", "60"))
WATCH_COOLDOWN = int(os.getenv("WATCH_COOLDOWN_MIN", "180"))

@dataclass
class Candidate:
    signal: base.Signal
    mode: str


def session_status() -> tuple[bool, str]:
    utc = datetime.now(ZoneInfo("UTC")); ny = utc.astimezone(ZoneInfo("America/New_York"))
    opened = ny.weekday() < 5 and ((ny.hour > 9 or (ny.hour == 9 and ny.minute >= 30)) and ny.hour < 16)
    return opened, f"UTC {utc:%Y-%m-%d %H:%M} | New York {ny:%Y-%m-%d %H:%M %Z}"


def fetch_all(symbols: list[str]) -> dict[str, base.BarData | None]:
    out: dict[str, base.BarData | None] = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(base.fetch_15m, s): s for s in symbols}
        for fut in as_completed(futures):
            s = futures[fut]
            try: out[s] = fut.result()
            except Exception as exc: log.warning("fetch %s: %s", s, exc); out[s] = None
    return out


def aligned_1h(d: base.BarData) -> base.BarData:
    buckets: dict[int, list[int]] = {}
    for i, ts in enumerate(d.ts): buckets.setdefault(ts // 3600, []).append(i)
    groups = [idx for _, idx in sorted(buckets.items()) if len(idx) >= 3]
    return base.BarData(ts=[d.ts[g[0]] for g in groups], open=[d.open[g[0]] for g in groups], high=[max(d.high[i] for i in g) for g in groups], low=[min(d.low[i] for i in g) for g in groups], close=[d.close[g[-1]] for g in groups], volume=[sum(d.volume[i] for i in g) for g in groups])


def mean(xs: list[float]) -> float: return sum(xs) / len(xs) if xs else 0.0


def rel_strength(d: base.BarData, spy: base.BarData | None, bars: int = 8) -> float:
    if not spy or len(d.close) <= bars or len(spy.close) <= bars: return 0.0
    return ((d.close[-1] / d.close[-bars-1]) - (spy.close[-1] / spy.close[-bars-1])) * 100


def prebreakout(symbol: str, d: base.BarData, spy: base.BarData | None) -> Candidate | None:
    if len(d.close) < 80: return None
    p = d.close[-1]; a = base.atr(d, 20)
    if a <= 0 or p <= 0: return None
    e21 = base.ema(d.close, 21); h1 = aligned_1h(d)
    t15, lbl15 = base.trend_score(d); t60, lbl60 = base.trend_score(h1) if len(h1.close) >= 25 else (0, "NEUTRAL")
    hi, lo = max(d.high[-21:-1]), min(d.low[-21:-1])
    dist_hi, dist_lo = (hi-p)/a, (p-lo)/a
    ranges = [max(0.0, h-l) for h,l in zip(d.high[-30:], d.low[-30:])]
    compression = mean(ranges[-6:]) / max(mean(ranges[-24:-6]), 1e-9)
    vol_avg = mean(d.volume[-21:-1]); recent_med = median(d.volume[-6:-1]) if len(d.volume) >= 7 else vol_avg; last_vol = d.volume[-1]
    volume_reclaim = last_vol >= max(vol_avg*1.08, recent_med*1.20); quiet = recent_med <= vol_avg*0.95
    accum = compression <= 0.86 and quiet and volume_reclaim
    higher_lows = d.low[-1] > d.low[-3] and d.low[-3] >= d.low[-5]; lower_highs = d.high[-1] < d.high[-3] and d.high[-3] <= d.high[-5]
    rs = rel_strength(d, spy)
    long_score = short_score = 0; lr=["PRE-BREAKOUT"]; sr=["PRE-BREAKOUT"]
    if p > e21[-1] and e21[-1] >= e21[-5]: long_score += 15; lr.append("EMA21 haussière")
    if p < e21[-1] and e21[-1] <= e21[-5]: short_score += 15; sr.append("EMA21 baissière")
    if t15 > 0: long_score += 8; lr.append("momentum 15m")
    if t15 < 0: short_score += 8; sr.append("momentum 15m baissier")
    if t60 > 0: long_score += 7; lr.append("biais 1h")
    if t60 < 0: short_score += 7; sr.append("biais 1h baissier")
    if 0 <= dist_hi <= 1.20: long_score += 20; lr.append(f"résistance {dist_hi:.2f} ATR")
    if 0 <= dist_lo <= 1.20: short_score += 20; sr.append(f"support {dist_lo:.2f} ATR")
    if compression <= 0.90: long_score += 7; short_score += 7; lr.append("compression"); sr.append("compression")
    if accum: long_score += 12; short_score += 12; lr.append("accumulation + reprise volume"); sr.append("distribution + reprise volume")
    if higher_lows: long_score += 10; lr.append("creux ascendants")
    if lower_highs: short_score += 10; sr.append("sommets descendants")
    if rs > 0.15: long_score += 8; lr.append("force relative")
    if rs < -0.15: short_score += 8; sr.append("faiblesse relative")
    if 0.5 <= a/p*100 <= 6: long_score += 5; short_score += 5
    side = "BUY" if long_score >= short_score else "SELL"; score=max(long_score, short_score)
    if score < CANDIDATE_MIN: return None
    if side == "BUY":
        stop=min(p-1.30*a, min(d.low[-8:])-0.10*a)
        if not 0 < stop < p: return None
        r=p-stop; tp1,tp2=p+r,p+2*r; reasons=lr
    else:
        stop=max(p+1.30*a, max(d.high[-8:])+0.10*a)
        if stop <= p: return None
        r=stop-p; tp1,tp2=p-r,p-2*r; reasons=sr
    sig=base.Signal(symbol,side,min(100,int(score)),p,stop,tp1,tp2,r,a,a/p*100,last_vol/vol_avg if vol_avg else 0,lbl15,lbl60,reasons)
    return Candidate(sig,"PRE-BREAKOUT")


def breadth(data: dict[str, base.BarData | None]) -> float:
    up=total=0
    for s in base.SYMBOLS:
        d=data.get(s)
        if not d or len(d.close)<30: continue
        e=base.ema(d.close,21); total+=1
        if d.close[-1]>e[-1] and e[-1]>=e[-5]: up+=1
    return up/max(total,1)


def overlay(data):
    def ret(x,n=4): return ((x.close[-1]/x.close[-n-1])-1)*100 if x and len(x.close)>n else 0.0
    spy,qqq,vix,uup,tlt=[data.get(x) for x in ("SPY","QQQ","^VIX","UUP","TLT")]; m=0; reasons=[]
    for x,lab,pos,neg in ((spy,"SPY",.4,-.4),(qqq,"QQQ",.5,-.5)):
        r=ret(x)
        if r>pos: m+=2; reasons.append(f"{lab} positif")
        elif r<neg: m-=2; reasons.append(f"{lab} faible")
    rv=ret(vix)
    if rv<=-1: m+=2; reasons.append("VIX baisse")
    elif rv>=2: m-=3; reasons.append("VIX hausse")
    ru,rt=ret(uup),ret(tlt)
    if ru<-.25: m+=1
    elif ru>.5: m-=1
    if rt>.5: m+=1
    elif rt<-.8: m-=1
    m=max(-8,min(8,m)); regime="RISK-ON" if m>=4 else "RISK-OFF" if m<=-4 else "MIXTE"; inst=0
    for x,lab in ((spy,"SPY"),(qqq,"QQQ")):
        if x and len(x.volume)>21:
            vr=x.volume[-1]/max(mean(x.volume[-21:-1]),1)
            if vr>=1.35: inst += 2 if ret(x)>0 else -2; reasons.append(f"volume {lab} anormal")
    br=breadth(data)
    if br>=.62: inst+=2; reasons.append("breadth forte")
    elif br<=.38: inst-=2; reasons.append("breadth faible")
    inst=max(-8,min(8,inst)); ilabel="ACCUMULATION_PROXY" if inst>=3 else "DISTRIBUTION_PROXY" if inst<=-3 else "NEUTRAL_PROXY"
    vals=[ret(data[e]) for e in macro.SECTOR_ETFS.values() if data.get(e)]; sec=2 if mean(vals)>.35 else -2 if mean(vals)<-.35 else 0
    return m,regime,inst,ilabel,sec,br,reasons[-8:]


def adjust(c,ov,data):
    m,regime,inst,ilabel,sec,br,_=ov; direction=1 if c.signal.side=="BUY" else -1; score=c.signal.score+direction*(2*m+2*inst+2*sec)
    etf=macro.SECTOR_MAP.get(c.signal.symbol)
    if etf and data.get(etf) and data.get(c.signal.symbol):
        rs=((data[c.signal.symbol].close[-1]/data[c.signal.symbol].close[-9])-1)*100-((data[etf].close[-1]/data[etf].close[-9])-1)*100
        if direction*rs>.20: score+=3; c.signal.reason.append(f"force relative vs {etf}")
    if regime=="RISK-OFF" and c.signal.side=="BUY": score-=3; c.signal.reason.append("macro risk-off")
    if regime=="RISK-ON" and c.signal.side=="SELL": score-=3; c.signal.reason.append("macro risk-on")
    c.signal.score=max(0,min(100,int(round(score)))); c.signal.reason=c.signal.reason[-7:]
    if c.mode=="CONFIRMÉ" and c.signal.score<FINAL_MIN: return None
    return c


def fmt(c,ov):
    s=c.signal; icon="🟢" if s.side=="BUY" else "🔴"; title="🔥 PRE-BREAKOUT / ACCUMULATION" if c.mode=="PRE-BREAKOUT" else "✅ SIGNAL CONFIRMÉ"
    return (f"{icon} {title} — {s.side} {s.symbol}\n━━━━━━━━━━━━━━━━━━\nScore: {s.score}/100\nPrix: {s.price:.2f}\nSL: {s.stop:.2f}\nTP1: {s.tp1:.2f}\nTP2: {s.tp2:.2f}\nATR: {s.atr_pct:.2f}%\nVolume: {s.volume_ratio:.1f}x\nTrend 15m: {s.trend15}\nTrend 1h: {s.trend60}\nConfluence: {' • '.join(s.reason)}\nMacro: {ov[1]} ({ov[0]:+d})\nInstitutional proxy: {ov[3]} ({ov[2]:+d})\nBreadth: {ov[5]*100:.0f}%\n⚠️ Analytique uniquement — aucun ordre Trading 212 n'est exécuté.")


def send(c,ov,state,now,cooldown):
    key=f"{c.mode}:{c.signal.symbol}:{c.signal.side}"; prev=state.get(key,{}); last=float(prev.get("sent_at",0))
    if now-last<cooldown: return False
    ok=base.telegram_send(fmt(c,ov))
    if ok: state[key]={"sent_at":now,"price":c.signal.price,"score":c.signal.score}
    return ok


def main()->int:
    if not base.TELEGRAM_BOT_TOKEN or not base.TELEGRAM_CHAT_ID: return 2
    is_open,clock=session_status()
    if not is_open:
        base.telegram_send(f"ℹ️ Trading 212 Scanner: marché US fermé.\n{clock}\nSession régulière: 09:30–16:00 New York.")
        return 0
    symbols=list(dict.fromkeys(base.SYMBOLS+macro.MACRO_SYMBOLS+list(macro.SECTOR_ETFS.values()))); data=fetch_all(symbols); spy=data.get("SPY"); base.MIN_SCORE=CANDIDATE_MIN; candidates=[]
    for s in base.SYMBOLS:
        d=data.get(s)
        if not d: continue
        sig=base.build_signal(s,d,spy)
        if sig: candidates.append(Candidate(sig,"CONFIRMÉ"))
        pb=prebreakout(s,d,spy)
        if pb: candidates.append(pb)
    best={}
    for c in candidates:
        key=(c.signal.symbol,c.signal.side)
        if key not in best or c.signal.score>best[key].signal.score: best[key]=c
    candidates=list(best.values()); ov=overlay(data)
    enhanced=[x for x in (adjust(c,ov,data) for c in candidates) if x]; enhanced.sort(key=lambda x:x.signal.score,reverse=True)
    state=base.load_state(); now=time.time(); sent=0
    confirmed=[c for c in enhanced if c.mode=="CONFIRMÉ" and c.signal.score>=FINAL_MIN]; watches=[c for c in enhanced if c.mode=="PRE-BREAKOUT" and c.signal.score>=WATCH_MIN]
    for c in confirmed[:MAX_ALERTS]: sent+=int(send(c,ov,state,now,COOLDOWN))
    if not confirmed:
        for c in watches[:WATCH_ALERTS]: sent+=int(send(c,ov,state,now,WATCH_COOLDOWN))
    base.save_state(state)
    base.telegram_send(f"📊 Scan V2: {sum(v is not None for v in data.values())}/{len(data)} données | candidats {len(candidates)} | confirmés {len(confirmed)} | pré-breakout {len(watches)} | envoyés {sent} | {clock}")
    return 0

if __name__=="__main__": raise SystemExit(main())
