"""Forex Intraday Scanner v2 — D1/H4/H1/M15.

D1 = strategic bias; H4 = structure; H1 = setup; M15 = entry trigger.
Adds DXY, US 10Y, VIX, SPY, cross-pair currency strength, session filter,
ATR volatility and structure-based risk/reward. No RSI/MACD.
Alerting only: no broker order execution.
"""
from __future__ import annotations
import os, time, logging
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from statistics import mean
import requests
import telegram_signals as base

LOG = logging.getLogger("forex-v2")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
YAHOO="https://query1.finance.yahoo.com/v8/finance/chart"
S=requests.Session(); S.headers.update({"User-Agent":"Mozilla/5.0 T212Forex/2.0","Accept":"application/json"})
PAIRS={
 "EURUSD=X":("EUR","USD","EUR/USD"),"GBPUSD=X":("GBP","USD","GBP/USD"),"USDJPY=X":("USD","JPY","USD/JPY"),
 "USDCHF=X":("USD","CHF","USD/CHF"),"AUDUSD=X":("AUD","USD","AUD/USD"),"NZDUSD=X":("NZD","USD","NZD/USD"),
 "USDCAD=X":("USD","CAD","USD/CAD"),"EURGBP=X":("EUR","GBP","EUR/GBP"),"EURJPY=X":("EUR","JPY","EUR/JPY"),
 "GBPJPY=X":("GBP","JPY","GBP/JPY"),"AUDJPY=X":("AUD","JPY","AUD/JPY"),"CADJPY=X":("CAD","JPY","CAD/JPY")}
DXY="DX-Y.NYB"; US10Y="^TNX"; VIX="^VIX"; SPY="SPY"
FINAL_MIN=int(os.getenv("FOREX_FINAL_MIN","72")); MAX_ALERTS=int(os.getenv("FOREX_MAX_ALERTS","3")); COOLDOWN=int(os.getenv("FOREX_COOLDOWN_MIN","240"))

@dataclass
class Bars:
 ts:list[int]; open:list[float]; high:list[float]; low:list[float]; close:list[float]; volume:list[float]
@dataclass
class Signal:
 pair:str; symbol:str; side:str; score:int; price:float; sl:float; tp1:float; tp2:float; rr:float
 d1:str; h4:str; h1:str; m15:str; dxy:str; macro:str; strength:str; session:str; reasons:list[str]

def fetch(symbol, interval, range_):
 try:
  r=S.get(f"{YAHOO}/{symbol}",params={"range":range_,"interval":interval,"includePrePost":"false","events":"div,splits"},timeout=15); r.raise_for_status()
  q=r.json()["chart"]["result"][0]["indicators"]["quote"][0]; ts=q and r.json()["chart"]["result"][0].get("timestamp",[]); rows=[]
  for i,t in enumerate(ts):
   vals=(q["open"][i],q["high"][i],q["low"][i],q["close"][i],q["volume"][i])
   if all(v is not None for v in vals): rows.append((int(t),*map(float,vals)))
  if len(rows)<60:return None
  return Bars([x[0] for x in rows],[x[1] for x in rows],[x[2] for x in rows],[x[3] for x in rows],[x[4] for x in rows],[x[5] for x in rows])
 except Exception as e:
  LOG.warning("%s %s: %s",symbol,interval,e); return None

def resample_h4(h1:Bars|None)->Bars|None:
 if not h1:return None
 buckets={}
 for i,t in enumerate(h1.ts):
  # UTC 4-hour bucket
  key=t-(t%(4*3600)); buckets.setdefault(key,[]).append(i)
 rows=[]
 for key in sorted(buckets):
  idx=buckets[key]
  if len(idx)<3:continue
  rows.append((key,h1.open[idx[0]],max(h1.high[i] for i in idx),min(h1.low[i] for i in idx),h1.close[idx[-1]],sum(h1.volume[i] for i in idx)))
 if len(rows)<60:return None
 return Bars([x[0] for x in rows],[x[1] for x in rows],[x[2] for x in rows],[x[3] for x in rows],[x[4] for x in rows],[x[5] for x in rows])

def ema(v,p):
 if not v:return []
 k=2/(p+1);o=[v[0]]
 for x in v[1:]:o.append(x*k+o[-1]*(1-k))
 return o

def atr(d,p=14):
 if not d or len(d.close)<p+1:return 0
 tr=[];prev=d.close[0]
 for h,l,c in zip(d.high[1:],d.low[1:],d.close[1:]):tr.append(max(h-l,abs(h-prev),abs(l-prev)));prev=c
 return mean(tr[-p:])

def ret(d,n):return ((d.close[-1]/d.close[-n-1])-1)*100 if d and len(d.close)>n else 0

def trend(d):
 if not d or len(d.close)<205:return 0
 e20,e50,e200=ema(d.close,20),ema(d.close,50),ema(d.close,200);p=d.close[-1]
 if p>e20[-1]>e50[-1]>e200[-1]:return 2
 if p>e50[-1]>e200[-1]:return 1
 if p<e20[-1]<e50[-1]<e200[-1]:return -2
 if p<e50[-1]<e200[-1]:return -1
 return 0

def session():
 h=datetime.now(timezone.utc).hour+datetime.now(timezone.utc).minute/60
 if 7<=h<12:return "LONDRES"
 if 12<=h<17:return "LONDRES + NEW YORK"
 if 17<=h<21:return "NEW YORK"
 return "HORS_SESSION"

def macro(m):
 dxy,us10,vix,spy=m.get(DXY),m.get(US10Y),m.get(VIX),m.get(SPY);s=0;r=[]
 if ret(dxy,20)>1.5:s-=2;r.append("DXY fort")
 elif ret(dxy,20)<-1.5:s+=2;r.append("DXY faible")
 if ret(us10,20)>2:s-=1;r.append("taux US montent")
 elif ret(us10,20)<-2:s+=1;r.append("taux US baissent")
 if ret(vix,10)>8:s-=1;r.append("VIX haut")
 elif ret(vix,10)<-8:s+=1;r.append("VIX baisse")
 if ret(spy,20)>1:s+=1;r.append("risk-on")
 elif ret(spy,20)<-1:s-=1;r.append("risk-off")
 regime="RISK-ON" if s>=2 else "RISK-OFF" if s<=-2 else "MIXTE"
 return regime," • ".join(r) or "macro neutre"

def strength(ds):
 c={x:[] for x in ("EUR","GBP","USD","JPY","CHF","AUD","NZD","CAD")}
 for sym,(a,b,_) in PAIRS.items():
  r=ret(ds.get(sym),20);c[a].append(r);c[b].append(-r)
 return {k:(mean(v) if v else 0) for k,v in c.items()}

def build(sym,fr,st,regime,macro_reason):
 a,b,pair=PAIRS[sym];d1,h4,h1,m15=fr["d1"],fr["h4"],fr["h1"],fr["m15"]
 if not all((d1,h4,h1,m15)):return None
 td,th4,th1=trend(d1),trend(h4),trend(h1); a15=atr(m15);ah=atr(h1)
 if not a15 or not ah:return None
 dxy_r=ret(fr["DXY"],20); dxy="BULL" if dxy_r>1.5 else "BEAR" if dxy_r<-1.5 else "NEUTRAL"
 rel=st.get(a,0)-st.get(b,0); long=short=0;lr=[];sr=[]
 if td>0:long+=22;lr.append("D1 haussier")
 if td<0:short+=22;sr.append("D1 baissier")
 if th4>0:long+=18;lr.append("H4 haussier")
 if th4<0:short+=18;sr.append("H4 baissier")
 if th1>0:long+=15;lr.append("H1 haussier")
 if th1<0:short+=15;sr.append("H1 baissier")
 if rel>1:long+=12;lr.append(f"{a} fort / {b} faible")
 if rel<-1:short+=12;sr.append(f"{b} fort / {a} faible")
 if "USD" in (a,b):
  usd=1 if dxy_r< -1.5 else -1 if dxy_r>1.5 else 0
  if a=="USD": usd*=-1
  if b=="USD": usd*=-1
  if usd>0:long+=8;lr.append("DXY confirme")
  if usd<0:short+=8;sr.append("DXY confirme")
 if regime=="RISK-ON" and a in ("AUD","NZD","CAD"):long+=4;lr.append("macro pro-cyclique")
 if regime=="RISK-ON" and b in ("JPY","CHF"):long+=3;lr.append("macro pro-cyclique")
 if regime=="RISK-OFF" and b in ("JPY","CHF"):short+=4;sr.append("macro défensif")
 if regime=="RISK-OFF" and a in ("JPY","CHF"):long+=3;lr.append("macro défensif")
 side="BUY" if long>short else "SELL";score=max(long,short)
 if score<FINAL_MIN:return None
 sess=session()
 if sess=="HORS_SESSION":return None
 p=m15.close[-1];e20=ema(m15.close,20)[-1];hi=max(m15.high[-9:-1]);lo=min(m15.low[-9:-1])
 if side=="BUY":
  trig=p>hi or (p>e20 and m15.close[-1]>m15.close[-2]>m15.close[-3])
  if not trig:return None
  sl=min(lo-0.35*a15,p-1.15*a15);risk=max(p-sl,a15);tp1=p+1.8*risk;tp2=p+3*risk;rr=1.8
 else:
  trig=p<lo or (p<e20 and m15.close[-1]<m15.close[-2]<m15.close[-3])
  if not trig:return None
  sl=max(hi+0.35*a15,p+1.15*a15);risk=max(sl-p,a15);tp1=p-1.8*risk;tp2=p-3*risk;rr=1.8
 return Signal(pair,sym,side,min(100,score+5),p,sl,tp1,tp2,rr,"BULLISH" if td>0 else "BEARISH" if td<0 else "MIXTE","BULLISH" if th4>0 else "BEARISH" if th4<0 else "MIXTE","BULLISH" if th1>0 else "BEARISH" if th1<0 else "MIXTE","CONFIRMÉ",dxy,regime,f"{a} {rel:+.1f} vs {b}",sess,lr if side=="BUY" else sr)

def fmt(s):
 icon="🟢" if s.side=="BUY" else "🔴";act="ACHAT" if s.side=="BUY" else "VENTE"
 return (f"{icon} SIGNAL FOREX INTRADAY — {s.pair}\n━━━━━━━━━━━━━━━━━━\nSTRATÉGIE : D1 + H4 + H1 + M15\nDirection : {act}\nScore : {s.score}/100\n"
 f"Entrée : {s.price:.5f}\nSL : {s.sl:.5f}\nTP1 : {s.tp1:.5f}\nTP2 : {s.tp2:.5f}\nR:R TP1 : 1:{s.rr:.1f}\n"
 f"D1 : {s.d1} | H4 : {s.h4} | H1 : {s.h1} | M15 : {s.m15}\nDXY : {s.dxy}\nForce relative : {s.strength}\nMacro : {s.macro}\nSession : {s.session}\n"
 f"Confluence : {' • '.join(s.reasons[:8])}\n⚠️ Analyse uniquement — aucun ordre Forex n'est exécuté.")

def main():
 if not base.TELEGRAM_BOT_TOKEN or not base.TELEGRAM_CHAT_ID:return 2
 market_syms=list(PAIRS)+[DXY,US10Y,VIX,SPY]; data={s:{} for s in PAIRS}; market={}
 jobs=[]
 with ThreadPoolExecutor(max_workers=12) as ex:
  for s in PAIRS:
   jobs += [(s,"d1",ex.submit(fetch,s,"1d","2y")),(s,"h1",ex.submit(fetch,s,"1h","6mo")),(s,"m15",ex.submit(fetch,s,"15m","10d"))]
  for s in (DXY,US10Y,VIX,SPY): jobs.append((s,"macro",ex.submit(fetch,s,"1d","1y")))
  for s,k,f in jobs:
   try:d=f.result()
   except Exception:d=None
   if s in data:data[s][k]=d
   else:market[s]=d
 for s in PAIRS:
  data[s]["h4"]=resample_h4(data[s].get("h1"))
  data[s].update(market)
 st=strength({s:data[s].get("d1") for s in PAIRS});reg,reason=macro(market);c=[]
 for s in PAIRS:
  sig=build(s,data[s],st,reg,reason)
  if sig:c.append(sig)
 c.sort(key=lambda x:x.score,reverse=True);state=base.load_state();now=time.time();sent=0
 for sig in c[:MAX_ALERTS]:
  key=f"FXV2:{sig.pair}:{sig.side}";last=float(state.get(key,{}).get("sent_at",0))
  if now-last<COOLDOWN*60:continue
  if base.telegram_send(fmt(sig)):state[key]={"sent_at":now,"price":sig.price,"score":sig.score};sent+=1
 base.save_state(state);base.telegram_send(f"💱 Scan FOREX D1+H4+H1+M15: {len(c)} signaux | envoyés {sent} | Macro {reg} | DXY {next((x.dxy for x in c),'non-confirmé')}")
 LOG.info("Forex V2: candidates=%d sent=%d",len(c),sent);return 0

if __name__=="__main__":raise SystemExit(main())
