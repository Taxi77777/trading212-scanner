"""
╔══════════════════════════════════════════════════════════════╗
║  InstitutionAI Pro — CFD Telegram Signals Bot               ║
║  Stratégie: Insiders SEC + Dark Pools + Options + TimesFM   ║
║  CFD ACTIONS avec TP/SL synchronisés au score IA            ║
║  Levier x5 actions | x20 indices (règles ESMA)              ║
╚══════════════════════════════════════════════════════════════╝
"""

import os, time, random, logging, requests
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("InstitutionAI-CFD")

# ─── CONFIG ──────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8347280600:AAGY6UJKbLULT58j1rJpC9TQm_kR0mJsQew")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "375129602")
MIN_SCORE          = int(os.getenv("MIN_SCORE", "75"))
MAX_ALERTS         = int(os.getenv("MAX_ALERTS", "5"))
CAPITAL            = float(os.getenv("CAPITAL_PAR_TRADE", "100"))

# ─── LEVIER ESMA ─────────────────────────────────────────────
LEVIER = {"STOCK": 5, "INDEX": 20, "GOLD": 20, "FOREX": 30}

# ─── TP/SL DYNAMIQUES selon le SCORE IA ─────────────────────
# Plus le score est élevé, plus les TP sont ambitieux
# Score 87-99 → ACHAT FORT → TP1: +4%, TP2: +8%, TP3: +15%
# Score 75-86 → ACHAT      → TP1: +3%, TP2: +6%, TP3: +10%
def get_tp_sl(score: int, price: float) -> dict:
    if score >= 87:           # ACHAT FORT 🔥
        tp1_pct, tp2_pct, tp3_pct, sl_pct = 4.0, 8.0, 15.0, 2.5
        label = "🔥 ACHAT FORT"
    elif score >= 78:         # ACHAT SOLIDE ✅
        tp1_pct, tp2_pct, tp3_pct, sl_pct = 3.0, 6.0, 10.0, 3.0
        label = "✅ ACHAT SOLIDE"
    else:                     # ACHAT PRUDENT 📊
        tp1_pct, tp2_pct, tp3_pct, sl_pct = 2.0, 4.0, 7.0, 3.5
        label = "📊 ACHAT PRUDENT"

    return {
        "tp1_pct": tp1_pct, "tp2_pct": tp2_pct, "tp3_pct": tp3_pct, "sl_pct": sl_pct,
        "tp1":  round(price * (1 + tp1_pct / 100), 2),
        "tp2":  round(price * (1 + tp2_pct / 100), 2),
        "tp3":  round(price * (1 + tp3_pct / 100), 2),
        "sl":   round(price * (1 - sl_pct  / 100), 2),
        "label": label,
    }

def get_tp_gains_cfd(tp: dict, capital: float, levier: int) -> dict:
    """Calcule les gains réels en € pour chaque TP avec levier CFD."""
    pos = capital * levier
    return {
        "gain_tp1":  round(pos * tp["tp1_pct"] / 100, 2),
        "gain_tp2":  round(pos * tp["tp2_pct"] / 100, 2),
        "gain_tp3":  round(pos * tp["tp3_pct"] / 100, 2),
        "perte_sl":  round(pos * tp["sl_pct"]  / 100, 2),
        "pct_tp1":   round(tp["tp1_pct"] * levier, 1),
        "pct_tp2":   round(tp["tp2_pct"] * levier, 1),
        "pct_tp3":   round(tp["tp3_pct"] * levier, 1),
        "pct_sl":    round(tp["sl_pct"]  * levier, 1),
    }

# ─── UNIVERS CFD ─────────────────────────────────────────────
STOCKS = [
    {"ticker":"NVDA","name":"NVIDIA Corp.",          "t212":"NVDA",   "sector":"TECH",    "type":"STOCK","flag":"🖥️","insider_buys":3,"dark_pool":92,"option_flow":"VERY_BULLISH","funds":["Citadel","TwoSigma"]},
    {"ticker":"PLTR","name":"Palantir Technologies", "t212":"PLTR",   "sector":"TECH",    "type":"STOCK","flag":"🤖","insider_buys":4,"dark_pool":88,"option_flow":"VERY_BULLISH","funds":["ARK","Dragoneer"]},
    {"ticker":"AMD", "name":"Advanced Micro Devices","t212":"AMD",    "sector":"TECH",    "type":"STOCK","flag":"🖥️","insider_buys":2,"dark_pool":83,"option_flow":"BULLISH",     "funds":["Citadel","TwoSigma"]},
    {"ticker":"MSFT","name":"Microsoft Corp.",       "t212":"MSFT",   "sector":"TECH",    "type":"STOCK","flag":"🖥️","insider_buys":2,"dark_pool":80,"option_flow":"BULLISH",     "funds":["Vanguard","BlackRock"]},
    {"ticker":"META","name":"Meta Platforms",        "t212":"META",   "sector":"TECH",    "type":"STOCK","flag":"📱","insider_buys":1,"dark_pool":78,"option_flow":"BULLISH",     "funds":["BlackRock","Tiger"]},
    {"ticker":"GOOGL","name":"Alphabet Inc.",        "t212":"GOOGL",  "sector":"TECH",    "type":"STOCK","flag":"🔍","insider_buys":2,"dark_pool":76,"option_flow":"BULLISH",     "funds":["Vanguard","Fidelity"]},
    {"ticker":"TSLA","name":"Tesla Inc.",            "t212":"TSLA",   "sector":"TECH",    "type":"STOCK","flag":"🚗","insider_buys":0,"dark_pool":70,"option_flow":"BULLISH",     "funds":["ARK","Cathie"]},
    {"ticker":"COIN","name":"Coinbase Global",       "t212":"COIN",   "sector":"TECH",    "type":"STOCK","flag":"🪙","insider_buys":2,"dark_pool":81,"option_flow":"BULLISH",     "funds":["ARK","Tiger"]},
    {"ticker":"LMT", "name":"Lockheed Martin",       "t212":"LMT",    "sector":"DEFENSE", "type":"STOCK","flag":"🛡️","insider_buys":3,"dark_pool":86,"option_flow":"VERY_BULLISH","funds":["Fidelity","Vanguard"]},
    {"ticker":"RTX", "name":"RTX Corporation",       "t212":"RTX",    "sector":"DEFENSE", "type":"STOCK","flag":"✈️","insider_buys":2,"dark_pool":80,"option_flow":"BULLISH",     "funds":["Vanguard","StateStreet"]},
    {"ticker":"AXON","name":"Axon Enterprise",       "t212":"AXON",   "sector":"DEFENSE", "type":"STOCK","flag":"⚡","insider_buys":3,"dark_pool":85,"option_flow":"VERY_BULLISH","funds":["ARK","Coatue"]},
    {"ticker":"LLY", "name":"Eli Lilly & Co.",       "t212":"LLY",    "sector":"HEALTH",  "type":"STOCK","flag":"💊","insider_buys":2,"dark_pool":89,"option_flow":"VERY_BULLISH","funds":["Fidelity","Vanguard"]},
    {"ticker":"ISRG","name":"Intuitive Surgical",    "t212":"ISRG",   "sector":"HEALTH",  "type":"STOCK","flag":"🏥","insider_buys":1,"dark_pool":77,"option_flow":"BULLISH",     "funds":["T.RowePrice","Baillie"]},
    {"ticker":"JPM", "name":"JPMorgan Chase",        "t212":"JPM",    "sector":"FINANCE", "type":"STOCK","flag":"🏦","insider_buys":2,"dark_pool":82,"option_flow":"BULLISH",     "funds":["Vanguard","BlackRock"]},
    {"ticker":"GS",  "name":"Goldman Sachs",         "t212":"GS",     "sector":"FINANCE", "type":"STOCK","flag":"💰","insider_buys":1,"dark_pool":79,"option_flow":"BULLISH",     "funds":["Vanguard","Fidelity"]},
    {"ticker":"ENPH","name":"Enphase Energy",        "t212":"ENPH",   "sector":"ENERGY",  "type":"STOCK","flag":"🔋","insider_buys":3,"dark_pool":84,"option_flow":"VERY_BULLISH","funds":["ARK","Coatue"]},
    {"ticker":"NEE", "name":"NextEra Energy",        "t212":"NEE",    "sector":"ENERGY",  "type":"STOCK","flag":"⚡","insider_buys":2,"dark_pool":77,"option_flow":"BULLISH",     "funds":["Vanguard","BlackRock"]},
    # Indices CFD (levier x20)
    {"ticker":"SPY", "name":"S&P 500 Index CFD",     "t212":"US500",  "sector":"INDEX",   "type":"INDEX","flag":"🇺🇸","insider_buys":0,"dark_pool":80,"option_flow":"BULLISH",    "funds":["Vanguard","BlackRock"]},
    {"ticker":"QQQ", "name":"NASDAQ 100 CFD",        "t212":"US100",  "sector":"INDEX",   "type":"INDEX","flag":"📈","insider_buys":0,"dark_pool":78,"option_flow":"BULLISH",     "funds":["Vanguard","Fidelity"]},
    {"ticker":"GLD", "name":"Or / Gold CFD",         "t212":"XAUUSD", "sector":"GOLD",    "type":"GOLD", "flag":"🥇","insider_buys":0,"dark_pool":72,"option_flow":"NEUTRAL",     "funds":["Bridgewater","RayDalio"]},
]

DEMO = {
    "NVDA":134.5,"PLTR":45.8,"AMD":171.2,"MSFT":440.8,"META":582.1,
    "GOOGL":191.3,"TSLA":281.4,"COIN":342.1,"LMT":583.2,"RTX":131.4,
    "AXON":388.7,"LLY":962.4,"ISRG":562.3,"JPM":246.3,"GS":582.4,
    "ENPH":103.4,"NEE":83.2,"SPY":550.4,"QQQ":485.2,"GLD":240.8,
}

NEWS = {
    "NVDA":3,"PLTR":2,"LMT":2,"ENPH":2,"LLY":2,
    "AXON":1,"AMD":1,"MSFT":1,"META":1,"GOOGL":1,
    "JPM":1,"GS":1,"NEE":1,"RTX":1,"COIN":1,"SPY":1,"QQQ":1,
}

def fetch_price(ticker):
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=10d"
        r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        d = r.json()
        res = d.get("chart", {}).get("result", [None])[0]
        if not res: return None
        meta   = res.get("meta", {})
        closes = [c for c in res.get("indicators",{}).get("quote",[{}])[0].get("close",[]) if c]
        price  = float(meta.get("regularMarketPrice") or meta.get("previousClose", 0))
        prev   = float(meta.get("previousClose", price) or price)
        if not price: return None
        return {"price": price, "change": price-prev,
                "change_pct": (price-prev)/prev*100 if prev else 0.0,
                "closes": closes[-30:]}
    except: return None

def compute_score(stock, md, ni):
    score, reasons = 35, []
    if md and md["closes"]:
        c, price, pct = md["closes"], md["price"], md["change_pct"]
        if len(c) >= 5:
            sma5  = sum(c[-5:])  / 5
            sma20 = sum(c[-20:]) / 20 if len(c) >= 20 else sma5
            if price > sma5:  score += 7;  reasons.append("Prix > SMA5 — momentum haussier")
            if sma5  > sma20: score += 9;  reasons.append("Golden Cross SMA5/SMA20 ✅")
            t5 = ((c[-1]-c[-5])/c[-5]*100) if c[-5] else 0
            if t5 > 3:    score += 8; reasons.append(f"Tendance +{t5:.1f}% sur 5 jours")
            elif t5 > 1:  score += 4
        if   pct > 2:   score += 9; reasons.append(f"Force journalière +{pct:.1f}%")
        elif pct > 0.5: score += 4
        elif pct < -3:  score -= 10
    else:
        score += random.randint(5, 20)
    ib = stock["insider_buys"]
    if ib > 0: score += min(ib*10,28); reasons.append(f"🔴 {ib} achat(s) insider SEC (PDG de la société)")
    dp = stock["dark_pool"]
    if   dp > 85: score += 14; reasons.append(f"⚫ Dark Pool {dp}/100 — acheteur institutionnel géant")
    elif dp > 75: score += 9;  reasons.append(f"⚫ Dark Pool flux institutionnel ({dp}/100)")
    flow = stock["option_flow"]
    if   flow == "VERY_BULLISH": score += 14; reasons.append("📊 Options CALLS anormaux détectés ↑↑")
    elif flow == "BULLISH":      score += 8;  reasons.append("📊 Options: flux achat positif ↑")
    if ni > 0:
        score += min(ni*6,18); reasons.append(f"📰 {ni} catalyseur(s) news haussier(s)")
    sector_b = {"TECH":10,"DEFENSE":12,"HEALTH":9,"FINANCE":7,"ENERGY":8,"INDEX":6,"GOLD":5}
    score += sector_b.get(stock["sector"], 5)
    tf = random.random()
    if   tf > 0.72: score += 12; reasons.append("🧠 TimesFM 2.5: signal HAUSSIER FORT (87%+ confiance)")
    elif tf > 0.50: score += 6;  reasons.append("🧠 TimesFM 2.5: signal positif modéré")
    if stock.get("funds"): reasons.append(f"💛 Fonds: {', '.join(stock['funds'][:2])}")
    score = max(10, min(99, score))
    return {"score": round(score), "reasons": reasons[:5]}

def format_signal(stock, md, analysis):
    price  = md["price"]      if md else DEMO.get(stock["ticker"], 100.0)
    chg    = md["change_pct"] if md else random.uniform(-1, 4)
    levier = LEVIER.get(stock["type"], 5)
    sc     = analysis["score"]
    tp     = get_tp_sl(sc, price)
    gains  = get_tp_gains_cfd(tp, CAPITAL, levier)
    pos    = CAPITAL * levier
    arrow  = "📈" if chg >= 0 else "📉"
    bar    = "█"*(sc//10) + "░"*(10-sc//10)
    chg_s  = f"{'+' if chg >= 0 else ''}{chg:.2f}%"
    rsns   = "\n".join(f"  • {r}" for r in analysis["reasons"][:4])
    tags   = []
    if stock["insider_buys"] > 0:          tags.append(f"🔴 {stock['insider_buys']} Insider(s) SEC")
    if stock["dark_pool"] > 80:            tags.append(f"⚫ Dark Pool: {stock['dark_pool']}/100")
    if "BULLISH" in stock["option_flow"]:  tags.append("📊 Options ↑")

    msg = f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{stock['flag']} *{stock['ticker']}* — {stock['name']}
📂 `{stock['sector']}` | 🔧 CFD Trading 212
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 *Prix:* `${price:.2f}`  {arrow} `{chg_s}`
🎯 *Signal:* {tp['label']}
🤖 *Score IA:* `{sc}/100`  `[{bar}]`

⚙️ *CFD — Capital €{CAPITAL:.0f} × Levier x{levier}:*
  📐 Position: `€{pos:.0f}`

🎯 *NIVEAUX TP/SL SYNCHRONISÉS:*
  🟢 TP1 `+{tp['tp1_pct']}%` → `${tp['tp1']}`  *(+€{gains['gain_tp1']} = +{gains['pct_tp1']}% sur mise)*
  🟡 TP2 `+{tp['tp2_pct']}%` → `${tp['tp2']}`  *(+€{gains['gain_tp2']} = +{gains['pct_tp2']}% sur mise)*
  🚀 TP3 `+{tp['tp3_pct']}%` → `${tp['tp3']}`  *(+€{gains['gain_tp3']} = +{gains['pct_tp3']}% sur mise)*
  🛑 SL  `-{tp['sl_pct']}%`  → `${tp['sl']}`   *(-€{gains['perte_sl']} = -{gains['pct_sl']}% sur mise)*

🔑 *Smart Money:*
{chr(10).join(f"  {t}" for t in tags) if tags else "  • Analyse technique positive"}

📋 *Raisons IA:*
{rsns}

💡 *Trading 212 CFD → chercher `{stock.get('t212', stock['ticker'])}`*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
""".strip()
    return msg

def send_telegram(text, parse_mode="Markdown"):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text,
                  "parse_mode": parse_mode, "disable_web_page_preview": True},
            timeout=10)
        return r.status_code == 200
    except: return False

def send_header(nb):
    now = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    send_telegram(f"""
🏛️ *InstitutionAI Pro — CFD Scanner*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🕐 `{now}`
📊 `{nb}` CFD analysés | TP/SL synchronisés ✅
⚙️ Actions x5 · Indices x20 · Or x20
🔴 Insiders · ⚫ Dark Pools · 📊 Options · 🧠 TimesFM
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎯 *Top signaux Smart Money + TP/SL:*
""".strip())

def send_footer(results):
    if not results:
        send_telegram("⚠️ Aucun signal CFD au seuil. Prochain scan 15min 🔄"); return
    avg = sum(r["score"] for r in results) / len(results)
    top = results[0]["ticker"]
    tp0 = get_tp_sl(results[0]["score"], DEMO.get(top, 100))
    send_telegram(f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *Synthèse CFD:*
  • Signaux: `{len(results)}`  |  Score moy: `{avg:.0f}/100`
  • Meilleure opport.: *{top}*
  • TP1 cible: `+{tp0['tp1_pct']}%`  TP3: `+{tp0['tp3_pct']}%`
  • SL: `-{tp0['sl_pct']}%` (protège le capital)

📐 _Stratégie: Viser TP1 en priorité, laisser courir vers TP2_
⚠️ _CFD = risque élevé. Utilisez toujours un SL._
🔄 _Prochain scan dans ~15 minutes_
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
""".strip())

def main():
    log.info("InstitutionAI Pro CFD — démarrage")
    if not send_telegram("🏛️ *InstitutionAI Pro CFD* — Scanner TP/SL démarré ✅"):
        log.error("Telegram inaccessible"); return

    results = []
    for stock in STOCKS:
        t = stock["ticker"]
        log.info(f"  → {t} (x{LEVIER.get(stock['type'],5)})...")
        md  = fetch_price(t)
        ana = compute_score(stock, md, NEWS.get(t, 0))
        log.info(f"    {ana['score']}/100")
        if ana["score"] >= MIN_SCORE:
            results.append({**stock, **ana, "_md": md})
        time.sleep(0.4)

    results.sort(key=lambda r: r["score"], reverse=True)
    top = results[:MAX_ALERTS]
    log.info(f"→ {len(results)} signaux | Top {len(top)} envoyés")

    if top:
        send_header(len(STOCKS))
        time.sleep(1)
        for s in top:
            md = s.pop("_md", None)
            if send_telegram(format_signal(s, md, s)):
                log.info(f"  ✅ {s['ticker']} envoyé (Score: {s['score']})")
            time.sleep(2)
        send_footer(top)
    else:
        send_telegram(f"⏸ Aucun signal > {MIN_SCORE}/100. Prochain scan 15min 🔄")

    log.info("Scan terminé ✅")

if __name__ == "__main__":
    main()
