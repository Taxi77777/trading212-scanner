from __future__ import annotations

"""Walk-forward backtest for the Forex v3/v4 signal engine.

Uses Yahoo historical bars, completed-bar discipline, next-bar entry and
bar-by-bar SL/TP resolution. Central-bank-rate overlay is reported separately
because the current repository rate source is point-in-time, not historical.
"""

import json, math, statistics, time
from datetime import datetime, timezone
from pathlib import Path
import requests
import forex_intraday_scanner_v3 as scanner

YAHOO = scanner.YAHOO
OUT = Path("backtest_results.json")
PAIRS = {
    "EURUSD=X": ("EUR","USD","EUR/USD"), "GBPUSD=X": ("GBP","USD","GBP/USD"),
    "USDJPY=X": ("USD","JPY","USD/JPY"), "USDCHF=X": ("USD","CHF","USD/CHF"),
    "AUDUSD=X": ("AUD","USD","AUD/USD"), "NZDUSD=X": ("NZD","USD","NZD/USD"),
    "USDCAD=X": ("USD","CAD","USD/CAD"), "EURGBP=X": ("EUR","GBP","EUR/GBP"),
    "EURJPY=X": ("EUR","JPY","EUR/JPY"), "GBPJPY=X": ("GBP","JPY","GBP/JPY"),
    "AUDJPY=X": ("AUD","JPY","AUD/JPY"), "CADJPY=X": ("CAD","JPY","CAD/JPY"),
    "EURCHF=X": ("EUR","CHF","EUR/CHF"), "EURAUD=X": ("EUR","AUD","EUR/AUD"),
    "EURNZD=X": ("EUR","NZD","EUR/NZD"), "GBPAUD=X": ("GBP","AUD","GBP/AUD"),
    "GBPCAD=X": ("GBP","CAD","GBP/CAD"), "GBPCHF=X": ("GBP","CHF","GBP/CHF"),
    "GBPNZD=X": ("GBP","NZD","GBP/NZD"), "AUDCAD=X": ("AUD","CAD","AUD/CAD"),
    "AUDCHF=X": ("AUD","CHF","AUD/CHF"), "CADCHF=X": ("CAD","CHF","CAD/CHF"),
    "NZDCAD=X": ("NZD","CAD","NZD/CAD"), "NZDCHF=X": ("NZD","CHF","NZD/CHF"),
}

session = requests.Session(); session.headers.update({"User-Agent":"Mozilla/5.0 T212ForexBacktest/1.0"})

def fetch(symbol, interval, range_):
    r = session.get(f"{YAHOO}/{symbol}", params={"range":range_,"interval":interval,"includePrePost":"false","events":"div,splits"}, timeout=20)
    r.raise_for_status(); res = r.json()["chart"]["result"][0]
    ts = res.get("timestamp",[]); q = res["indicators"]["quote"][0]; rows=[]
    for i,t in enumerate(ts):
        vals=(q["open"][i],q["high"][i],q["low"][i],q["close"][i],q["volume"][i])
        if all(v is not None for v in vals): rows.append((int(t),*map(float,vals)))
    return scanner.Bars([x[0] for x in rows],[x[1] for x in rows],[x[2] for x in rows],[x[3] for x in rows],[x[4] for x in rows],[x[5] for x in rows])

def cut(d, end_ts):
    if not d:return None
    idx=[i for i,t in enumerate(d.ts) if t<=end_ts]
    if not idx:return None
    n=idx[-1]+1
    return scanner.Bars(d.ts[:n],d.open[:n],d.high[:n],d.low[:n],d.close[:n],d.volume[:n])

def completed_daily(d, close_time):
    if not d:return None
    idx=[]
    for i,t in enumerate(d.ts):
        if t+86400<=close_time: idx.append(i)
    if not idx:return None
    n=idx[-1]+1
    return scanner.Bars(d.ts[:n],d.open[:n],d.high[:n],d.low[:n],d.close[:n],d.volume[:n])

def completed_h1(d, close_time):
    if not d:return None
    idx=[]
    for i,t in enumerate(d.ts):
        if t+3600<=close_time: idx.append(i)
    if not idx:return None
    n=idx[-1]+1
    return scanner.Bars(d.ts[:n],d.open[:n],d.high[:n],d.low[:n],d.close[:n],d.volume[:n])

def session_for(ts):
    dt=datetime.fromtimestamp(ts,tz=timezone.utc); h=dt.hour+dt.minute/60
    if 7<=h<12:return "LONDRES"
    if 12<=h<17:return "LONDRES + NEW YORK"
    if 17<=h<21:return "NEW YORK"
    return "HORS_SESSION"

def resolve_trade(m15, start_idx, side, entry, sl, tp1, max_bars=96):
    last=min(len(m15.close)-1,start_idx+max_bars)
    for i in range(start_idx,last+1):
        hi,lo=m15.high[i],m15.low[i]
        if side=="BUY": sl_hit=lo<=sl; tp_hit=hi>=tp1
        else: sl_hit=hi>=sl; tp_hit=lo<=tp1
        if sl_hit and tp_hit:return -1.0,"AMBIGUOUS_SAME_BAR"
        if sl_hit:return -1.0,"SL"
        if tp_hit:return 1.7,"TP1"
    last_close=m15.close[last]
    r=(last_close-entry)/(entry-sl) if side=="BUY" else (entry-last_close)/(sl-entry)
    return float(r),"TIMEOUT"

def main():
    scanner.PAIRS=PAIRS; scanner.FINAL_MIN=68; scanner.SETUP_MIN=54
    raw={s:{"d1":fetch(s,"1d","2y"),"h1":fetch(s,"1h","60d"),"m15":fetch(s,"15m","60d")} for s in PAIRS}
    market={s:fetch(s,"1d","2y") for s in (scanner.DXY,scanner.US10Y,scanner.VIX,scanner.SPY)}
    # These market frames are daily and intentionally treated as known only up to the signal date.
    results=[]; evaluated=0; candidates=0
    for sym,fr in raw.items():
        d1,h1,m15=fr["d1"],fr["h1"],fr["m15"]
        for i in range(250,len(m15.ts)-1):
            close_time=m15.ts[i]+900
            d1c=completed_daily(d1,close_time); h1c=completed_h1(h1,close_time)
            if not d1c or not h1c or len(d1c.close)<205 or len(h1c.close)<205:continue
            h4c=scanner.resample_h4(h1c); mcut=cut(m15,m15.ts[i])
            if not h4c or len(h4c.close)<60 or not mcut or len(mcut.close)<60:continue
            # Macro series are cut to the same completed date/time to avoid using future data.
            market_cut={k:completed_daily(v,close_time) for k,v in market.items()}
            frames={"d1":d1c,"h4":h4c,"h1":h1c,"m15":mcut,**market_cut}
            strength=scanner.currency_strength({s:completed_daily(raw[s]["d1"],close_time) for s in PAIRS})
            macro,reason=scanner.macro_regime(market_cut)
            old_session=scanner.session_name; scanner.session_name=lambda:session_for(close_time)
            try:sig=scanner.build_signal(sym,frames,strength,macro,reason,"BACKTEST_NO_NEWS",False)
            finally:scanner.session_name=old_session
            evaluated+=1
            if sig is None:continue
            candidates+=1
            if sig.state!="ENTREE":continue
            entry=m15.open[i+1]
            r,exit_reason=resolve_trade(m15,i+1,sig.side,entry,sig.sl,sig.tp1)
            results.append({"pair":sig.pair,"side":sig.side,"score":sig.score,"r":r,"exit":exit_reason,"ts":m15.ts[i]})
    by={}
    for x in results:by.setdefault(x["pair"],[]).append(x)
    def stats(arr):
        if not arr:return {"trades":0,"win_rate":0,"profit_factor":0,"expectancy_r":0,"max_dd_r":0}
        wins=[x["r"] for x in arr if x["r"]>0]; losses=[x["r"] for x in arr if x["r"]<0]
        eq=peak=dd=0
        for x in arr:
            eq+=x["r"];peak=max(peak,eq);dd=max(dd,peak-eq)
        pf=(sum(wins)/abs(sum(losses))) if losses else math.inf
        return {"trades":len(arr),"win_rate":round(100*len(wins)/len(arr),2),"profit_factor":round(pf,3) if math.isfinite(pf) else "inf","expectancy_r":round(statistics.mean(x["r"] for x in arr),4),"max_dd_r":round(dd,3)}
    report={"engine":"Forex v4 core; historical rate/news overlay excluded","universe":len(PAIRS),"period":"60d M15 / 2y D1","bars_evaluated":evaluated,"setups_seen":candidates,"overall":stats(results),"by_pair":{k:stats(v) for k,v in sorted(by.items())},"notes":["Higher-timeframe bars are completed before signal evaluation.","Entry assumed at next M15 open.","Same-bar SL+TP is conservatively counted as -1R.","Historical central-bank rates/news are excluded because the live repository sources are point-in-time, so applying current rates backward would introduce look-ahead bias."],"generated_at":time.time()}
    OUT.write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report,indent=2)); return 0

if __name__=="__main__":raise SystemExit(main())
