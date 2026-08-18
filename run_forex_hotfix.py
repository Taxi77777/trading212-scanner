"""Production wrapper for the Forex intraday scanner."""
from __future__ import annotations
import forex_intraday_scanner as s

s.FINAL_MIN = 64


def resample_h4(d):
    if not d or len(d.ts) < 80:
        return None
    buckets = {}
    for i, ts in enumerate(d.ts):
        key = ts - (ts % 14400)
        buckets.setdefault(key, []).append(i)
    rows = []
    for key in sorted(buckets):
        idx = buckets[key]
        rows.append((key, d.open[idx[0]], max(d.high[i] for i in idx), min(d.low[i] for i in idx), d.close[idx[-1]], sum(d.volume[i] for i in idx)))
    if len(rows) < 60:
        return None
    return s.Bars([r[0] for r in rows], [r[1] for r in rows], [r[2] for r in rows], [r[3] for r in rows], [r[4] for r in rows], [r[5] for r in rows])


def relaxed_score_pair(sym, frames, strength, macro_regime, macro_reason):
    base_ccy, quote_ccy, pair = s.PAIRS[sym]
    d1 = frames.get("1d"); h1 = frames.get("1h"); m15 = frames.get("15m")
    h4 = resample_h4(h1)
    if not all((d1, h4, h1, m15)):
        return None
    td1, th4, th1 = s.trend(d1), s.trend(h4), s.trend(h1)
    atr15 = s.atr(m15, 14)
    if atr15 <= 0:
        return None
    p = m15.close[-1]
    strong = strength.get(base_ccy, 0) - strength.get(quote_ccy, 0)
    dxy_r = s.ret(frames.get("DXY"), 20)
    dxy_bias = "DXY_BULL" if dxy_r > 1.5 else "DXY_BEAR" if dxy_r < -1.5 else "DXY_NEUTRAL"
    long_score = short_score = 0; lr=[]; sr=[]
    if td1 > 0: long_score += 20; lr.append("D1 haussier")
    if td1 < 0: short_score += 20; sr.append("D1 baissier")
    if th4 > 0: long_score += 18; lr.append("H4 haussier")
    if th4 < 0: short_score += 18; sr.append("H4 baissier")
    if th1 > 0: long_score += 16; lr.append("H1 haussier")
    if th1 < 0: short_score += 16; sr.append("H1 baissier")
    if strong > 0.75: long_score += 12; lr.append(f"{base_ccy} fort / {quote_ccy} faible")
    if strong < -0.75: short_score += 12; sr.append(f"{base_ccy} faible / {quote_ccy} fort")
    if "USD" in (base_ccy, quote_ccy):
        usd_bull = dxy_r > 1.0
        usd_bear = dxy_r < -1.0
        if base_ccy == "USD":
            if usd_bull: long_score += 7; lr.append("DXY confirme USD")
            if usd_bear: short_score += 7; sr.append("DXY contre USD")
        else:
            if usd_bear: long_score += 7; lr.append("DXY confirme devise")
            if usd_bull: short_score += 7; sr.append("DXY contre devise")
    if macro_regime == "RISK-ON":
        if base_ccy in ("AUD","NZD","CAD"): long_score += 4; lr.append("macro risk-on")
        if quote_ccy in ("JPY","CHF"): long_score += 3; lr.append("JPY/CHF défensif")
    if macro_regime == "RISK-OFF":
        if base_ccy in ("JPY","CHF"): long_score += 4; lr.append("macro défensif")
        if quote_ccy in ("AUD","NZD","CAD"): long_score += 3; lr.append("devise cyclique faible")
    side = "BUY" if long_score >= short_score else "SELL"
    score = max(long_score, short_score)
    if score < s.FINAL_MIN:
        return None
    recent_hi = max(m15.high[-13:-1]); recent_lo = min(m15.low[-13:-1]); e20 = s.ema(m15.close,20)[-1]
    if side == "BUY":
        trigger = p > recent_hi or (p > e20 and m15.close[-1] > m15.close[-2])
        timing = "M15_CONFIRMÉ" if trigger else "ATTENTE_M15"
        stop = min(recent_lo - 0.30*atr15, p - 1.10*atr15)
        risk = max(p-stop, atr15); tp1=p+1.8*risk; tp2=p+3.0*risk; rr=1.8
        reasons = lr
    else:
        trigger = p < recent_lo or (p < e20 and m15.close[-1] < m15.close[-2])
        timing = "M15_CONFIRMÉ" if trigger else "ATTENTE_M15"
        stop = max(recent_hi + 0.30*atr15, p + 1.10*atr15)
        risk = max(stop-p, atr15); tp1=p-1.8*risk; tp2=p-3.0*risk; rr=1.8
        reasons = sr
    session=s.session_name()
    if session == "HORS_SESSION": return None
    return s.FxSignal(sym,pair,side,min(100,score),p,stop,tp1,tp2,rr,
        "BULLISH" if td1>0 else "BEARISH" if td1<0 else "MIXTE",
        "BULLISH" if th4>0 else "BEARISH" if th4<0 else "MIXTE",
        "BULLISH" if th1>0 else "BEARISH" if th1<0 else "MIXTE",
        timing,session,macro_regime,dxy_bias,
        f"{base_ccy} {strong:+.1f} vs {quote_ccy}",reasons)


s.score_pair = relaxed_score_pair

if __name__ == "__main__":
    raise SystemExit(s.main())
