"""Macro/risk regime engine for the Trading 212 scanner.
Uses public Yahoo chart data only. No invented institutional positions.
Outputs a dynamic minimum score and a concise macro regime for Telegram.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from datetime import datetime, timezone
import requests

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
STATE = Path("macro_state.json")
BASE = "https://query1.finance.yahoo.com/v8/finance/chart"
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 T212Macro/1.0", "Accept": "application/json"})

# Proxies: SPY/QQQ = risk appetite, ^VIX = volatility stress,
# DX-Y.NYB = USD, ^TNX = US 10Y yield, CL=F = oil, GC=F = gold.
SYMBOLS = ["SPY", "QQQ", "^VIX", "DX-Y.NYB", "^TNX", "CL=F", "GC=F"]

def last_change(symbol: str):
    try:
        r = S.get(f"{BASE}/{symbol}", params={"range":"5d","interval":"1d","events":"history"}, timeout=10)
        r.raise_for_status(); x = r.json()["chart"]["result"][0]
        c = [v for v in x["indicators"]["quote"][0]["close"] if v is not None]
        if len(c) < 2: return None
        return float(c[-1]), (float(c[-1]) / float(c[-2]) - 1) * 100
    except Exception:
        return None

def telegram(text: str):
    if not TOKEN or not CHAT_ID: return
    try:
        S.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json={"chat_id":CHAT_ID,"text":text,"disable_web_page_preview":True}, timeout=10)
    except requests.RequestException:
        pass

def main():
    d = {k:last_change(k) for k in SYMBOLS}
    spy = d.get("SPY"); qqq = d.get("QQQ"); vix = d.get("^VIX"); usd = d.get("DX-Y.NYB"); tnx = d.get("^TNX"); oil = d.get("CL=F"); gold = d.get("GC=F")
    score = 0
    reasons=[]
    if spy and spy[1] > 0: score += 2; reasons.append("SPY +")
    elif spy and spy[1] < 0: score -= 2; reasons.append("SPY -")
    if qqq and qqq[1] > 0: score += 2; reasons.append("QQQ +")
    elif qqq and qqq[1] < 0: score -= 2; reasons.append("QQQ -")
    if vix:
        if vix[0] < 18: score += 2; reasons.append("VIX calme")
        elif vix[0] > 25: score -= 3; reasons.append("VIX stress")
        elif vix[0] > 20: score -= 1; reasons.append("VIX élevé")
    if usd and usd[1] > 0.5: score -= 1; reasons.append("USD fort")
    elif usd and usd[1] < -0.5: score += 1; reasons.append("USD faible")
    if tnx and tnx[1] > 1.0: score -= 1; reasons.append("taux US ↑")
    elif tnx and tnx[1] < -1.0: score += 1; reasons.append("taux US ↓")
    if oil and oil[1] > 2.0: score -= 1; reasons.append("pétrole ↑")
    if gold and gold[1] > 1.5: reasons.append("or ↑ / risk-off possible")

    if score >= 4: regime="RISK-ON"; min_score=60
    elif score <= -4: regime="RISK-OFF"; min_score=75
    else: regime="MIXTE"; min_score=65

    payload={"regime":regime,"score":score,"min_score":min_score,"reasons":reasons,"updated":datetime.now(timezone.utc).isoformat()}
    Path("macro.env").write_text(f"MIN_SCORE={min_score}\nMACRO_REGIME={regime}\nMACRO_SCORE={score}\n", encoding="utf-8")
    old={}
    if STATE.exists():
        try: old=json.loads(STATE.read_text(encoding="utf-8"))
        except Exception: pass
    if old.get("regime") != regime or old.get("score") != score:
        spy_txt=f"SPY {spy[1]:+.2f}%" if spy else "SPY n/d"
        qqq_txt=f"QQQ {qqq[1]:+.2f}%" if qqq else "QQQ n/d"
        vix_txt=f"VIX {vix[0]:.1f}" if vix else "VIX n/d"
        telegram(f"🌍 MACRO — {regime}\nScore macro: {score:+d}\n{spy_txt} • {qqq_txt} • {vix_txt}\n" + " • ".join(reasons[:6]) + f"\nSeuil scanner: {min_score}/100")
    STATE.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

if __name__ == "__main__": main()
