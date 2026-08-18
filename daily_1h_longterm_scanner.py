from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed
from statistics import mean, median

import requests
import telegram_signals as base

LOG = logging.getLogger("t212-daily-1h")
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0 T212Daily1H/1.0", "Accept": "application/json"})
CANDIDATE_MIN = int(os.getenv("DAILY_CANDIDATE_MIN", "58"))
FINAL_MIN = int(os.getenv("DAILY_FINAL_MIN", "68"))
MAX_ALERTS = int(os.getenv("MAX_ALERTS", "5"))
COOLDOWN_MIN = int(os.getenv("ALERT_COOLDOWN_MIN", "1440"))
RISK_PCT = float(os.getenv("RISK_PCT", "1.0"))
STATE_FILE = base.STATE_FILE

NAMES = {
"NVDA":"NVIDIA Corporation","AMD":"Advanced Micro Devices, Inc.","AVGO":"Broadcom Inc.","QCOM":"QUALCOMM Incorporated","MU":"Micron Technology, Inc.","MRVL":"Marvell Technology, Inc.","ARM":"Arm Holdings plc","INTC":"Intel Corporation","TSM":"Taiwan Semiconductor Manufacturing Company","ASML":"ASML Holding N.V.",
"MSFT":"Microsoft Corporation","GOOGL":"Alphabet Inc.","META":"Meta Platforms, Inc.","AMZN":"Amazon.com, Inc.","AAPL":"Apple Inc.","ORCL":"Oracle Corporation","CRM":"Salesforce, Inc.","ADBE":"Adobe Inc.","NOW":"ServiceNow, Inc.","SNOW":"Snowflake Inc.",
"PLTR":"Palantir Technologies Inc.","AI":"C3.ai, Inc.","BBAI":"BigBear.ai Holdings, Inc.","SOUN":"SoundHound AI, Inc.","IONQ":"IonQ, Inc.","CRWD":"CrowdStrike Holdings, Inc.","PANW":"Palo Alto Networks, Inc.","NET":"Cloudflare, Inc.","DDOG":"Datadog, Inc.","MDB":"MongoDB, Inc.",
"COIN":"Coinbase Global, Inc.","HOOD":"Robinhood Markets, Inc.","PYPL":"PayPal Holdings, Inc.","SQ":"Block, Inc.","MSTR":"Strategy Inc.","RIOT":"Riot Platforms, Inc.","MARA":"MARA Holdings, Inc.","SOFI":"SoFi Technologies, Inc.","NU":"Nu Holdings Ltd.","AFRM":"Affirm Holdings, Inc.",
"TSLA":"Tesla, Inc.","RIVN":"Rivian Automotive, Inc.","LCID":"Lucid Group, Inc.","UBER":"Uber Technologies, Inc.","LYFT":"Lyft, Inc.","NIO":"NIO Inc.","XPEV":"XPeng Inc.","GM":"General Motors Company","F":"Ford Motor Company","ABNB":"Airbnb, Inc.",
"LMT":"Lockheed Martin Corporation","RTX":"RTX Corporation","NOC":"Northrop Grumman Corporation","GD":"General Dynamics Corporation","BA":"The Boeing Company","HWM":"Howmet Aerospace Inc.","GE":"GE Aerospace","CAT":"Caterpillar Inc.","DE":"Deere & Company","ETN":"Eaton Corporation plc",
"LLY":"Eli Lilly and Company","NVO":"Novo Nordisk A/S","MRNA":"Moderna, Inc.","PFE":"Pfizer Inc.","ABBV":"AbbVie Inc.","JNJ":"Johnson & Johnson","ISRG":"Intuitive Surgical, Inc.","UNH":"UnitedHealth Group Incorporated","AMGN":"Amgen Inc.","GILD":"Gilead Sciences, Inc.",
"JPM":"JPMorgan Chase & Co.","BAC":"Bank of America Corporation","GS":"Goldman Sachs Group, Inc.","MS":"Morgan Stanley","V":"Visa Inc.","MA":"Mastercard Incorporated","WMT":"Walmart Inc.","COST":"Costco Wholesale Corporation","HD":"The Home Depot, Inc.","LOW":"Lowe's Companies, Inc.",
"XOM":"Exxon Mobil Corporation","CVX":"Chevron Corporation","COP":"ConocoPhillips","SLB":"SLB","NFLX":"Netflix, Inc.","DIS":"The Walt Disney Company","PEP":"PepsiCo, Inc.","KO":"The Coca-Cola Company"}

MACRO = ["SPY","QQQ","^VIX","UUP","TLT","GLD","USO"]
SECTORS = ["XLK","SMH","XLF","XLE","XLV","XLI","XLY","XLC","XLU","XLRE","XLB","XLP"]

@dataclass
class Bars:
    ts:list[int]; open:list[float]; high:list[float]; low:list[float]; close:list[float]; volume:list[float]
@dataclass
class Signal:
    symbol:str; side:str; score:int; price:float; entry_low:float; entry_high:float; stop:float; tp1:float; tp2:float; tp3:float; trend:str; timing:str; reason:list[str]

def fetch(symbol:str, interval:str, range_:str)->Bars|None:
    try:
        r=SESSION.get(f"{YAHOO}/{symbol}",params={"range":range_,"interval":interval,"includePrePost":"false","events":"div,splits"},timeout=15)
        r.raise_for_status(); item=r.json()["chart"]["result"][0]; ts=item.get("timestamp",[]); q=item["indicators"]["quote"][0]; rows=[]
        for i,t in enumerate(ts):
            vals=(q["open"][i],q["high"][i],q["low"][i],q["close"][i],q["volume"][i])
            if all(v is not None for v in vals): rows.append((int(t),*map(float,vals)))
        if len(rows)<40:return None
        return Bars([x[0] for x in rows],[x[1] for x in rows],[x[2] for x in rows],[x[3] for x in rows],[x[4] for x in rows],[x[5] for x in rows])
    except Exception as exc:
        LOG.warning("%s %s: %s",symbol,interval,exc); return None

def ema(v:list[float],p:int)->list[float]:
    if not v:return []
    k=2/(p+1);o=[v[0]]
    for x in v[1:]:o.append(x*k+o[-1]*(1-k))
    return o

def atr(d:Bars,p:int=14)->float:
    tr=[];prev=None
    for h,l,c in zip(d.high,d.low,d.close):
        tr.append(h-l if prev is None else max(h-l,abs(h-prev),abs(l-prev)));prev=c
    return mean(tr[-p:]) if len(tr)>=p else 0.0

def ret(d:Bars,n:int)->float:
    return ((d.close[-1]/d.close[-n-1])-1)*100 if d and len(d.close)>n else 0.0

def daily_master(symbol:str,d:Bars,spy:Bars|None)->Signal|None:
    if len(d.close)<220:return None
    p=d.close[-1]; e20,e50,e200=ema(d.close,20),ema(d.close,50),ema(d.close,200); a=atr(d,14)
    if a<=0:return None
    high20=max(d.high[-21:-1]); high60=max(d.high[-61:-1]); low20=min(d.low[-21:-1]); low60=min(d.low[-61:-1])
    volavg=mean(d.volume[-21:-1]); vr=d.volume[-1]/volavg if volavg else 0
    rs=ret(d,20)-(ret(spy,20) if spy else 0)
    long=short=0; lr=[]; sr=[]
    if p>e20[-1]>e50[-1]>e200[-1]: long+=25;lr.append("EMA20>50>200")
    elif p>e50[-1]>e200[-1]: long+=17;lr.append("tendance Daily haussière")
    if p<e20[-1]<e50[-1]<e200[-1]: short+=25;sr.append("EMA20<50<200")
    elif p<e50[-1]<e200[-1]: short+=17;sr.append("tendance Daily baissière")
    if p>high20: long+=20;lr.append("breakout 20 jours")
    if p>high60: long+=10;lr.append("breakout 60 jours")
    if p<low20: short+=20;sr.append("cassure 20 jours")
    if p<low60: short+=10;sr.append("cassure 60 jours")
    if vr>=1.5:
        (long if p>e20[-1] else short)
        if p>e20[-1]: long+=10;lr.append(f"volume {vr:.1f}x")
        else: short+=10;sr.append(f"volume {vr:.1f}x")
    if rs>2: long+=10;lr.append("force relative vs SPY")
    if rs<-2: short+=10;sr.append("faiblesse relative vs SPY")
    side="BUY" if long>=short else "SELL"; score=max(long,short)
    if score<CANDIDATE_MIN:return None
    # Long-term daily structure. Entry is an actionable zone, not a single candle price.
    if side=="BUY":
        stop=min(low20-0.25*a,e50[-1]-1.2*a); risk=max(p-stop,0.5*a)
        if stop<=0 or stop>=p:return None
        # Use structural extensions; TP1/2/3 are intentionally wider than the old intraday model.
        tp1=max(p+2*risk, high60+0.5*a); tp2=max(p+3*risk,tp1+1.5*a); tp3=max(p+4.5*risk,tp2+2*a)
        return Signal(symbol,"BUY",min(100,score),p,max(p-0.5*a,p-0.03*p),p+0.5*a,stop,tp1,tp2,tp3,"BULLISH","WAIT_1H",lr)
    stop=max(high20+0.25*a,e50[-1]+1.2*a);risk=max(stop-p,0.5*a)
    if stop<=p:return None
    tp1=min(p-2*risk,low60-0.5*a);tp2=min(p-3*risk,tp1-1.5*a);tp3=min(p-4.5*risk,tp2-2*a)
    return Signal(symbol,"SELL",min(100,score),p,max(p-0.5*a,p-0.03*p),p+0.5*a,stop,tp1,tp2,tp3,"BEARISH","WAIT_1H",sr)

def hourly_trigger(symbol:str,d:Bars,master:Signal)->bool:
    if not d or len(d.close)<60:return False
    p=d.close[-1]; e20=ema(d.close,20); a=atr(d,14)
    if a<=0:return False
    prevhigh=max(d.high[-13:-1]); prevlow=min(d.low[-13:-1]); volavg=mean(d.volume[-21:-1]); vr=d.volume[-1]/volavg if volavg else 0
    if master.side=="BUY":
        trend=p>e20[-1] and e20[-1]>=e20[-5]
        breakout=p>prevhigh or (p>e20[-1] and d.close[-1]>d.close[-2] and d.low[-1]>=d.low[-3])
        return trend and breakout and vr>=0.9
    trend=p<e20[-1] and e20[-1]<=e20[-5]
    breakdown=p<prevlow or (p<e20[-1] and d.close[-1]<d.close[-2] and d.high[-1]<=d.high[-3])
    return trend and breakdown and vr>=0.9

def macro_regime(data:dict[str,Bars|None])->tuple[str,int,str]:
    spy,qqq,vix,uup,tlt=[data.get(x) for x in MACRO];score=0;reasons=[]
    for d,name,th in ((spy,"SPY",0.8),(qqq,"QQQ",1.0)):
        r=ret(d,20)
        if r>th:score+=2;reasons.append(f"{name} +{r:.1f}%/20j")
        elif r<-th:score-=2;reasons.append(f"{name} {r:.1f}%/20j")
    rv=ret(vix,10)
    if rv>8:score-=2;reasons.append("VIX monte")
    elif rv<-8:score+=1;reasons.append("VIX baisse")
    ru=ret(uup,20)
    if ru>1.5:score-=1;reasons.append("dollar fort")
    elif ru<-1.0:score+=1;reasons.append("dollar moins contraignant")
    rt=ret(tlt,20)
    if rt>1.0:score+=1;reasons.append("taux moins contraignants")
    elif rt<-1.5:score-=1;reasons.append("pression des taux")
    regime="RISK-ON" if score>=3 else "RISK-OFF" if score<=-3 else "MIXTE"
    return regime,score," • ".join(reasons[-5:])

def breadth(data:dict[str,Bars|None])->float:
    ok=tot=0
    for s in base.SYMBOLS:
        d=data.get(s)
        if not d or len(d.close)<200:continue
        e=ema(d.close,200);tot+=1;ok+=int(d.close[-1]>e[-1])
    return ok/max(tot,1)

def inst_proxy(symbol:str,d:Bars,spy:Bars|None)->tuple[int,str]:
    score=0; reasons=[];vr=d.volume[-1]/mean(d.volume[-21:-1]) if len(d.volume)>21 and mean(d.volume[-21:-1]) else 0
    rs=ret(d,20)-(ret(spy,20) if spy else 0)
    if vr>=1.5 and d.close[-1]>d.close[-5]:score+=2;reasons.append("volume relatif")
    if rs>2:score+=2;reasons.append("surperformance")
    if rs<-2:score-=2;reasons.append("sous-performance")
    label="ACCUMULATION_PROXY" if score>=3 else "DISTRIBUTION_PROXY" if score<=-2 else "NEUTRAL_PROXY"
    return score,label

def format_signal(sig:Signal,regime:str,br:float,inst_label:str,inst_score:int,name:str)->str:
    icon="🟢" if sig.side=="BUY" else "🔴"; action="ACHAT LONG TERME" if sig.side=="BUY" else "VENTE / COUVERTURE"
    return (f"{icon} {action} — {sig.symbol} — {name}\n━━━━━━━━━━━━━━━━━━\n"
            f"Horizon: plusieurs semaines à plusieurs mois\nScore: {sig.score}/100\nPrix actuel: {sig.price:.2f}\n"
            f"Zone d'entrée 1H: {sig.entry_low:.2f} – {sig.entry_high:.2f}\nSL structure Daily: {sig.stop:.2f}\n"
            f"TP1: {sig.tp1:.2f}\nTP2: {sig.tp2:.2f}\nTP3: {sig.tp3:.2f}\n"
            f"Daily: {sig.trend}\nDéclencheur 1H: {sig.timing}\nMacro: {regime}\n"
            f"Institutionnel proxy: {inst_label} ({inst_score:+d})\nBreadth > EMA200: {br*100:.0f}%\n"
            f"Confluence: {' • '.join(sig.reason[:7])}\n⚠️ Analyse uniquement — aucun ordre Trading 212 n'est exécuté.")

def main()->int:
    if not base.TELEGRAM_BOT_TOKEN or not base.TELEGRAM_CHAT_ID:return 2
    symbols=list(dict.fromkeys(base.SYMBOLS+MACRO+SECTORS))
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs={ex.submit(fetch,s,'1d','2y'):s for s in symbols}; daily={}
        for f in as_completed(futs):
            s=futs[f]
            try:daily[s]=f.result()
            except Exception:daily[s]=None
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs={ex.submit(fetch,s,'1h','60d'):s for s in base.SYMBOLS}; hourly={}
        for f in as_completed(futs):
            s=futs[f]
            try:hourly[s]=f.result()
            except Exception:hourly[s]=None
    spy=daily.get('SPY'); regime,macro_score,macro_reason=macro_regime(daily); br=breadth(daily); candidates=[]
    for s in base.SYMBOLS:
        d=daily.get(s)
        if not d:continue
        master=daily_master(s,d,spy)
        if not master:continue
        h=hourly.get(s)
        trigger=hourly_trigger(s,h,master)
        # Macro can strengthen/weaken but cannot manufacture a Daily setup.
        adj=master.score + (4 if regime=='RISK-ON' and master.side=='BUY' else 4 if regime=='RISK-OFF' and master.side=='SELL' else -4 if regime=='RISK-OFF' and master.side=='BUY' else -4 if regime=='RISK-ON' and master.side=='SELL' else 0)
        inst_score,inst_label=inst_proxy(s,d,spy); adj+=inst_score*2; adj=max(0,min(100,int(adj))); master.score=adj
        if master.score<FINAL_MIN:continue
        if not trigger:continue
        master.timing='1H_CONFIRMÉ'; candidates.append((master,inst_score,inst_label))
    candidates.sort(key=lambda x:x[0].score,reverse=True); state=base.load_state(); now=time.time();sent=0
    for sig,inst_score,inst_label in candidates[:MAX_ALERTS]:
        key=f"DAILY1H:{sig.symbol}:{sig.side}"; prev=state.get(key,{}); last=float(prev.get('sent_at',0))
        if now-last<COOLDOWN_MIN*60:continue
        if base.telegram_send(format_signal(sig,regime,br,inst_label,inst_score,NAMES.get(sig.symbol,sig.symbol))):
            state[key]={'sent_at':now,'price':sig.price,'score':sig.score};sent+=1
    base.save_state(state)
    base.telegram_send(f"📈 Scan DAILY + 1H: {sum(v is not None for v in daily.values())}/{len(daily)} Daily | setups confirmés {len(candidates)} | envoyés {sent} | Macro {regime} ({macro_score:+d})")
    LOG.info("Daily+1H: setups=%d sent=%d",len(candidates),sent)
    return 0

if __name__=='__main__':raise SystemExit(main())