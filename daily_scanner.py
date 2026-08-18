"""Trading 212 Daily Position Scanner.

Primary horizon: daily bars, with weekly trend confirmation.
No RSI/MACD/Ichimoku; signal is based on trend, structure, breakout,
relative strength, volume, ATR risk and macro/institutional proxies.
Alert only: never places orders.
"""
from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import mean
from zoneinfo import ZoneInfo

import requests

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
FINAL_MIN = int(os.getenv("DAILY_MIN_SCORE", "70"))
WATCH_MIN = int(os.getenv("DAILY_WATCH_SCORE", "60"))
MAX_ALERTS = int(os.getenv("DAILY_MAX_ALERTS", "5"))
COOLDOWN_HOURS = int(os.getenv("DAILY_COOLDOWN_HOURS", "24"))
RISK_PCT = float(os.getenv("RISK_PCT", "1.0"))

YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0 T212Daily/1.0", "Accept": "application/json"})

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

NAMES = {
    "NVDA":"NVIDIA Corporation","AMD":"Advanced Micro Devices","AVGO":"Broadcom","QCOM":"Qualcomm","MU":"Micron Technology","MRVL":"Marvell Technology","ARM":"Arm Holdings","INTC":"Intel","TSM":"Taiwan Semiconductor Manufacturing","ASML":"ASML Holding",
    "MSFT":"Microsoft","GOOGL":"Alphabet Class A","META":"Meta Platforms","AMZN":"Amazon","AAPL":"Apple","ORCL":"Oracle","CRM":"Salesforce","ADBE":"Adobe","NOW":"ServiceNow","SNOW":"Snowflake",
    "PLTR":"Palantir Technologies","CRWD":"CrowdStrike","PANW":"Palo Alto Networks","NET":"Cloudflare","DDOG":"Datadog","MDB":"MongoDB","COIN":"Coinbase","HOOD":"Robinhood Markets","PYPL":"PayPal","SQ":"Block","MSTR":"Strategy","RIOT":"Riot Platforms","MARA":"MARA Holdings","SOFI":"SoFi Technologies","NU":"Nu Holdings","AFRM":"Affirm Holdings",
    "TSLA":"Tesla","RIVN":"Rivian Automotive","LCID":"Lucid Group","UBER":"Uber Technologies","LYFT":"Lyft","NIO":"NIO","XPEV":"XPeng","GM":"General Motors","F":"Ford Motor","ABNB":"Airbnb",
    "LMT":"Lockheed Martin","RTX":"RTX Corporation","NOC":"Northrop Grumman","GD":"General Dynamics","BA":"Boeing","HWM":"Howmet Aerospace","GE":"GE Aerospace","CAT":"Caterpillar","DE":"Deere & Company","ETN":"Eaton",
    "LLY":"Eli Lilly","NVO":"Novo Nordisk","MRNA":"Moderna","PFE":"Pfizer","ABBV":"AbbVie","JNJ":"Johnson & Johnson","ISRG":"Intuitive Surgical","UNH":"UnitedHealth Group","AMGN":"Amgen","GILD":"Gilead Sciences",
    "JPM":"JPMorgan Chase","BAC":"Bank of America","GS":"Goldman Sachs","MS":"Morgan Stanley","V":"Visa","MA":"Mastercard","WMT":"Walmart","COST":"Costco","HD":"Home Depot","LOW":"Lowe's",
    "XOM":"Exxon Mobil","CVX":"Chevron","COP":"ConocoPhillips","SLB":"SLB","NFLX":"Netflix","DIS":"Walt Disney","PEP":"PepsiCo","KO":"Coca-Cola",
}

MACRO = ["SPY","QQQ","^VIX","UUP","TLT","GLD","USO"]
SECTORS = ["XLK","SMH","XLF","XLE","XLV","XLI","XLY","XLC","XLU","XLRE","XLB"]

@dataclass
class DailyData:
    ts:list[int]; open:list[float]; high:list[float]; low:list[float]; close:list[float]; volume:list[float]

@dataclass
class DailySignal:
    symbol:str; side:str; score:int; price:float; stop:float; tp1:float; tp2:float; tp3:float; atr:float; atr_pct:float; volume_ratio:float; reasons:list[str]; mode:str


def fetch_daily(symbol:str)->DailyData|None:
    try:
        r=SESSION.get(f"{YAHOO}/{symbol}",params={"range":"2y","interval":"1d","includePrePost":"false","events":"div,splits"},timeout=15)
        r.raise_for_status(); item=r.json()["chart"]["result"][0]
        ts=item.get("timestamp",[]); q=item["indicators"]["quote"][0]
        rows=[]
        for i,t in enumerate(ts):
            vals=(q["open"][i],q["high"][i],q["low"][i],q["close"][i],q["volume"][i])
            if all(v is not None for v in vals): rows.append((int(t),*map(float,vals)))
        if len(rows)<220: return None
        return DailyData(*[[x[j] for x in rows] for j in range(5+1)])
    except Exception as exc:
        logging.warning("%s: %s",symbol,exc); return None


def ema(xs:list[float],p:int)->list[float]:
    k=2/(p+1); out=[xs[0]]
    for x in xs[1:]: out.append(x*k+out[-1]*(1-k))
    return out


def sma(xs:list[float],p:int)->float:
    return sum(xs[-p:])/p


def atr(d:DailyData,p:int=21)->float:
    tr=[]; prev=None
    for h,l,c in zip(d.high,d.low,d.close):
        tr.append(h-l if prev is None else max(h-l,abs(h-prev),abs(l-prev))); prev=c
    return sum(tr[-p:])/p if len(tr)>=p else 0.0


def weekly_from_daily(d:DailyData)->DailyData:
    idx=[]; last_week=None
    buckets=[]
    for i,t in enumerate(d.ts):
        dt=datetime.fromtimestamp(t,tz=timezone.utc); key=(dt.isocalendar().year,dt.isocalendar().week)
        if key!=last_week:
            buckets.append([]); last_week=key
        buckets[-1].append(i)
    buckets=[b for b in buckets if len(b)>=3]
    return DailyData([d.ts[b[0]] for b in buckets],[d.open[b[0]] for b in buckets],[max(d.high[i] for i in b) for b in buckets],[min(d.low[i] for i in b) for b in buckets],[d.close[b[-1]] for b in buckets],[sum(d.volume[i] for i in b) for b in buckets])


def relative_strength(d:DailyData,spy:DailyData|None,days:int=20)->float:
    if not spy or len(d.close)<=days or len(spy.close)<=days:return 0.0
    return ((d.close[-1]/d.close[-days-1]-1)-(spy.close[-1]/spy.close[-days-1]-1))*100


def signal(symbol:str,d:DailyData,spy:DailyData|None)->DailySignal|None:
    p=d.close[-1]; a=atr(d,21); e20=ema(d.close,20); e50=ema(d.close,50); e200=ema(d.close,200); w=weekly_from_daily(d); we20=ema(w.close,20) if len(w.close)>=20 else [w.close[-1]]
    if a<=0 or len(w.close)<20:return None
    vol_avg=mean(d.volume[-21:-1]); vr=d.volume[-1]/vol_avg if vol_avg else 0
    high20=max(d.high[-21:-1]); low20=min(d.low[-21:-1]); high55=max(d.high[-56:-1]); low55=min(d.low[-56:-1])
    rs=relative_strength(d,spy,20)
    long_score=0; short_score=0; lr=[]; sr=[]
    if p>e20[-1]: long_score+=10; lr.append("cours > EMA20")
    if p>e50[-1]: long_score+=10; lr.append("cours > EMA50")
    if p>e200[-1]: long_score+=15; lr.append("cours > EMA200")
    if e20[-1]>e50[-1]>e200[-1]: long_score+=10; lr.append("EMA20/50/200 alignées")
    if len(we20)>=5 and w.close[-1]>we20[-1]: long_score+=10; lr.append("tendance hebdo positive")
    if p>high20: long_score+=15; lr.append("breakout 20 jours")
    elif p>high20*0.985: long_score+=8; lr.append("pré-breakout 20 jours")
    if p>high55: long_score+=15; lr.append("breakout 55 jours")
    if vr>=1.5: long_score+=10; lr.append(f"volume {vr:.1f}x")
    if rs>0.5: long_score+=10; lr.append("force relative vs SPY")
    if p<e20[-1]: short_score+=10; sr.append("cours < EMA20")
    if p<e50[-1]: short_score+=10; sr.append("cours < EMA50")
    if p<e200[-1]: short_score+=15; sr.append("cours < EMA200")
    if e20[-1]<e50[-1]<e200[-1]: short_score+=10; sr.append("EMA20/50/200 baissières")
    if len(we20)>=5 and w.close[-1]<we20[-1]: short_score+=10; sr.append("tendance hebdo négative")
    if p<low20: short_score+=15; sr.append("cassure 20 jours")
    elif p<low20*1.015: short_score+=8; sr.append("pré-cassure 20 jours")
    if p<low55: short_score+=15; sr.append("cassure 55 jours")
    if vr>=1.5: short_score+=10; sr.append(f"volume {vr:.1f}x")
    if rs<-0.5: short_score+=10; sr.append("faiblesse relative vs SPY")
    side="BUY" if long_score>=short_score else "SELL"; score=max(long_score,short_score)
    mode="CONFIRMÉ" if score>=FINAL_MIN else "WATCH" if score>=WATCH_MIN else None
    if not mode:return None
    if side=="BUY" and not (p>e50[-1] and p>e200[-1] and w.close[-1]>=we20[-1]): return None
    if side=="SELL" and not (p<e50[-1] and p<e200[-1] and w.close[-1]<=we20[-1]): return None
    stop=min(p-2.2*a, min(d.low[-20:])-0.25*a) if side=="BUY" else max(p+2.2*a,max(d.high[-20:])+0.25*a)
    if side=="BUY":
        if stop<=0 or stop>=p:return None
        risk=p-stop; tp1=p+2*risk; tp2=p+3*risk; tp3=p+4.5*risk
    else:
        if stop<=p:return None
        risk=stop-p; tp1=p-2*risk; tp2=p-3*risk; tp3=p-4.5*risk
    return DailySignal(symbol,side,min(score,100),p,stop,tp1,tp2,tp3,a,a/p*100,vr,(lr if side=="BUY" else sr),mode)


def macro_regime(data:dict[str,DailyData|None])->tuple[str,list[str]]:
    def ret(x,n=20): return ((x.close[-1]/x.close[-n-1]-1)*100) if x and len(x.close)>n else 0
    spy,qqq,vix,uup,tlt=data.get("SPY"),data.get("QQQ"),data.get("^VIX"),data.get("UUP"),data.get("TLT")
    s=0; reasons=[]
    for x,lab in [(spy,"SPY"),(qqq,"QQQ")]:
        r=ret(x)
        if r>2:s+=1;reasons.append(f"{lab} 20j positif")
        elif r<-2:s-=1;reasons.append(f"{lab} 20j négatif")
    rv=ret(vix)
    if rv>10:s-=2;reasons.append("VIX en hausse")
    elif rv<-10:s+=1;reasons.append("VIX en baisse")
    ru=ret(uup)
    if ru>1:s-=1;reasons.append("dollar ferme")
    elif ru<-1:s+=1;reasons.append("dollar moins contraignant")
    rt=ret(tlt)
    if rt>2:s+=1;reasons.append("TLT favorable")
    elif rt<-2:s-=1;reasons.append("pression taux")
    return ("RISK-ON" if s>=2 else "RISK-OFF" if s<=-2 else "MIXTE"),reasons


def adjust(sig:DailySignal,regime:str)->DailySignal|None:
    score=sig.score; reason=sig.reasons[:]
    if regime=="RISK-OFF" and sig.side=="BUY": score-=6;reason.append("macro risk-off")
    if regime=="RISK-ON" and sig.side=="SELL": score-=6;reason.append("macro risk-on")
    sig.score=max(0,min(100,score));sig.reasons=reason[-8:]
    return sig if sig.score>=FINAL_MIN or (sig.mode=="WATCH" and sig.score>=WATCH_MIN) else None


def send(text:str)->bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:return False
    try:
        r=SESSION.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",json={"chat_id":TELEGRAM_CHAT_ID,"text":text,"disable_web_page_preview":True},timeout=15)
        return r.status_code==200
    except requests.RequestException:return False


def fmt(s:DailySignal,regime:str,macro_reasons:list[str])->str:
    direction="ACHAT" if s.side=="BUY" else "VENTE"; icon="🟢" if s.side=="BUY" else "🔴"; name=NAMES.get(s.symbol,s.symbol)
    return (f"{icon} {'SIGNAL DAILY' if s.mode=='CONFIRMÉ' else '👀 WATCH DAILY'} — {direction}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Action : {s.symbol} — {name}\n"
            f"Score : {s.score}/100\n"
            f"Prix : {s.price:.2f}\n"
            f"Stop structurel : {s.stop:.2f}\n"
            f"TP1 : {s.tp1:.2f} (2R)\n"
            f"TP2 : {s.tp2:.2f} (3R)\n"
            f"TP3 : {s.tp3:.2f} (4.5R)\n"
            f"ATR 21j : {s.atr:.2f} ({s.atr_pct:.1f}%)\n"
            f"Volume : {s.volume_ratio:.1f}x\n"
            f"Macro : {regime}\n"
            f"Confluence : {' • '.join(s.reasons)}\n"
            f"Contexte macro : {' • '.join(macro_reasons[:4])}\n"
            f"Horizon : semaines à mois\n"
            f"⚠️ Analytique uniquement — aucun ordre Trading 212 n'est exécuté.")


def main()->int:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:return 2
    symbols=list(dict.fromkeys(SYMBOLS+MACRO+SECTORS)); data={}
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures={ex.submit(fetch_daily,s):s for s in symbols}
        for f in as_completed(futures):
            s=futures[f]
            try:data[s]=f.result()
            except Exception:data[s]=None
    spy=data.get("SPY"); reg,rs=macro_regime(data); signals=[]
    for s in SYMBOLS:
        d=data.get(s)
        if d:
            sig=signal(s,d,spy)
            if sig:
                sig=adjust(sig,reg)
                if sig:signals.append(sig)
    signals.sort(key=lambda x:x.score,reverse=True); confirmed=[s for s in signals if s.mode=="CONFIRMÉ"]
    now=time.time(); sent=0
    state={}
    try:
        import json
        from pathlib import Path
        p=Path("daily_signal_state.json")
        state=json.loads(p.read_text()) if p.exists() else {}
    except Exception: state={}
    for s in (confirmed[:MAX_ALERTS] if confirmed else [x for x in signals if x.mode=="WATCH"][:3]):
        key=f"{s.symbol}:{s.side}"; last=float(state.get(key,0))
        if now-last<COOLDOWN_HOURS*3600:continue
        if send(fmt(s,reg,rs)):
            state[key]=now; sent+=1
    from pathlib import Path
    Path("daily_signal_state.json").write_text(__import__("json").dumps(state,indent=2))
    send(f"📊 DAILY SCAN — données {sum(v is not None for v in data.values())}/{len(data)} | signaux {len(signals)} | confirmés {len(confirmed)} | macro {reg} | envoyés {sent}")
    logging.info("Daily scan: %d signals, %d confirmed, %d sent",len(signals),len(confirmed),sent)
    return 0

if __name__=="__main__":raise SystemExit(main())
