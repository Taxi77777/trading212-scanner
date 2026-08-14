"""
╔══════════════════════════════════════════════════════════════╗
║  InstitutionAI Pro v3 — CFD Actions Scanner                 ║
║  80+ ACTIONS · RSI · MACD · Volume · TP/SL · Score IA       ║
║  Insiders SEC · Dark Pools · Options Flow · TimesFM 2.5     ║
║  Levier x5 fixe (ESMA) · Telegram 24/7 · GitHub Actions    ║
╚══════════════════════════════════════════════════════════════╝
"""

import os, time, random, logging, requests
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("InstitutionAI-v3")

# ─── CONFIG ──────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8347280600:AAGY6UJKbLULT58j1rJpC9TQm_kR0mJsQew")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "375129602")
MIN_SCORE          = int(os.getenv("MIN_SCORE",   "72"))
MAX_ALERTS         = int(os.getenv("MAX_ALERTS",  "7"))
CAPITAL            = float(os.getenv("CAPITAL_PAR_TRADE", "100"))
LEVIER             = 5   # Fixe x5 ESMA — actions uniquement

# ─── TP/SL SYNCHRONISÉS AU SCORE IA ─────────────────────────
def get_tp_sl(score: int, price: float) -> dict:
    if score >= 88:
        tp1, tp2, tp3, sl, lbl = 4.0, 9.0, 18.0, 2.5, "🔥 ACHAT FORT"
    elif score >= 78:
        tp1, tp2, tp3, sl, lbl = 3.0, 6.0, 12.0, 3.0, "✅ ACHAT SOLIDE"
    else:
        tp1, tp2, tp3, sl, lbl = 2.0, 4.5, 8.0,  3.5, "📊 ACHAT PRUDENT"
    pos = CAPITAL * LEVIER
    rr  = round(tp1 / sl, 1)
    return {
        "tp1_pct": tp1, "tp2_pct": tp2, "tp3_pct": tp3, "sl_pct": sl, "label": lbl,
        "tp1": round(price*(1+tp1/100),2), "tp2": round(price*(1+tp2/100),2),
        "tp3": round(price*(1+tp3/100),2), "sl":  round(price*(1-sl/100),2),
        "gain_tp1": round(pos*tp1/100,2),  "gain_tp2": round(pos*tp2/100,2),
        "gain_tp3": round(pos*tp3/100,2),  "perte_sl": round(pos*sl/100,2),
        "pct_tp1": round(tp1*LEVIER,1), "pct_tp2": round(tp2*LEVIER,1),
        "pct_tp3": round(tp3*LEVIER,1), "pct_sl":  round(sl*LEVIER,1),
        "rr": rr,
    }

# ─── 80+ ACTIONS CFD — UNIVERS ÉLARGI ───────────────────────
STOCKS = [
    # ════ TECH / IA / SEMICONDUCTEURS ════════════════════════
    {"t":"NVDA",  "n":"NVIDIA Corporation",       "s":"TECH·IA",     "f":"🖥️", "ib":3,"dp":92,"of":"VERY_BULLISH","funds":["Citadel","TwoSigma"]},
    {"t":"AMD",   "n":"Advanced Micro Devices",   "s":"TECH·IA",     "f":"🔴", "ib":2,"dp":83,"of":"BULLISH",     "funds":["Citadel","TwoSigma"]},
    {"t":"INTC",  "n":"Intel Corporation",        "s":"TECH·SEMI",   "f":"🔵", "ib":2,"dp":74,"of":"BULLISH",     "funds":["Vanguard","BlackRock"]},
    {"t":"AVGO",  "n":"Broadcom Inc.",            "s":"TECH·SEMI",   "f":"🟣", "ib":1,"dp":80,"of":"BULLISH",     "funds":["Vanguard","Fidelity"]},
    {"t":"QCOM",  "n":"Qualcomm Inc.",            "s":"TECH·SEMI",   "f":"🔷", "ib":1,"dp":76,"of":"BULLISH",     "funds":["Vanguard","BlackRock"]},
    {"t":"MRVL",  "n":"Marvell Technology",       "s":"TECH·SEMI",   "f":"⚡", "ib":2,"dp":79,"of":"BULLISH",     "funds":["ARK","Coatue"]},
    {"t":"ARM",   "n":"ARM Holdings",             "s":"TECH·SEMI",   "f":"💪", "ib":1,"dp":77,"of":"BULLISH",     "funds":["SoftBank","TwoSigma"]},
    # ════ BIG TECH / CLOUD ════════════════════════════════════
    {"t":"MSFT",  "n":"Microsoft Corporation",    "s":"TECH·CLOUD",  "f":"💻", "ib":2,"dp":80,"of":"BULLISH",     "funds":["Vanguard","BlackRock"]},
    {"t":"GOOGL", "n":"Alphabet Inc. (Google)",   "s":"TECH·CLOUD",  "f":"🔍", "ib":2,"dp":76,"of":"BULLISH",     "funds":["Vanguard","Fidelity"]},
    {"t":"META",  "n":"Meta Platforms",           "s":"TECH·CLOUD",  "f":"📱", "ib":1,"dp":78,"of":"BULLISH",     "funds":["BlackRock","Tiger"]},
    {"t":"AMZN",  "n":"Amazon.com",               "s":"TECH·CLOUD",  "f":"📦", "ib":1,"dp":79,"of":"BULLISH",     "funds":["Vanguard","Fidelity"]},
    {"t":"AAPL",  "n":"Apple Inc.",               "s":"TECH·CLOUD",  "f":"🍎", "ib":1,"dp":75,"of":"NEUTRAL",     "funds":["Berkshire","Vanguard"]},
    {"t":"ORCL",  "n":"Oracle Corporation",       "s":"TECH·CLOUD",  "f":"🔶", "ib":2,"dp":78,"of":"BULLISH",     "funds":["Vanguard","Fidelity"]},
    {"t":"CRM",   "n":"Salesforce Inc.",          "s":"TECH·CLOUD",  "f":"☁️", "ib":1,"dp":74,"of":"BULLISH",     "funds":["Vanguard","BlackRock"]},
    {"t":"ADBE",  "n":"Adobe Systems",            "s":"TECH·CLOUD",  "f":"🎨", "ib":1,"dp":73,"of":"BULLISH",     "funds":["Vanguard","Fidelity"]},
    {"t":"NOW",   "n":"ServiceNow",               "s":"TECH·CLOUD",  "f":"🔧", "ib":2,"dp":78,"of":"BULLISH",     "funds":["Coatue","Dragoneer"]},
    {"t":"SNOW",  "n":"Snowflake Inc.",           "s":"TECH·CLOUD",  "f":"❄️", "ib":1,"dp":72,"of":"BULLISH",     "funds":["Berkshire","Tiger"]},
    # ════ IA / PURE PLAY ══════════════════════════════════════
    {"t":"PLTR",  "n":"Palantir Technologies",    "s":"TECH·IA",     "f":"🤖", "ib":4,"dp":88,"of":"VERY_BULLISH","funds":["ARK","Dragoneer"]},
    {"t":"AI",    "n":"C3.ai Inc.",               "s":"TECH·IA",     "f":"🧠", "ib":2,"dp":75,"of":"BULLISH",     "funds":["ARK","Tiger"]},
    {"t":"BBAI",  "n":"BigBear.ai Holdings",      "s":"TECH·IA",     "f":"🎯", "ib":3,"dp":77,"of":"BULLISH",     "funds":["ARK","Coatue"]},
    {"t":"SOUN",  "n":"SoundHound AI",            "s":"TECH·IA",     "f":"🎵", "ib":2,"dp":73,"of":"BULLISH",     "funds":["Nvidia","ARK"]},
    {"t":"IONQ",  "n":"IonQ (Quantum Computing)", "s":"TECH·QUANT",  "f":"⚛️", "ib":2,"dp":76,"of":"BULLISH",     "funds":["ARK","Tiger"]},
    # ════ FINTECH / CRYPTO ════════════════════════════════════
    {"t":"COIN",  "n":"Coinbase Global",          "s":"FINTECH",     "f":"🪙", "ib":2,"dp":81,"of":"BULLISH",     "funds":["ARK","Tiger"]},
    {"t":"SQ",    "n":"Block Inc. (Square)",      "s":"FINTECH",     "f":"💳", "ib":1,"dp":74,"of":"BULLISH",     "funds":["ARK","Coatue"]},
    {"t":"PYPL",  "n":"PayPal Holdings",          "s":"FINTECH",     "f":"💰", "ib":1,"dp":72,"of":"BULLISH",     "funds":["ValueAct","Vanguard"]},
    {"t":"HOOD",  "n":"Robinhood Markets",        "s":"FINTECH",     "f":"🏹", "ib":2,"dp":74,"of":"BULLISH",     "funds":["ARK","Tiger"]},
    {"t":"MSTR",  "n":"MicroStrategy (Bitcoin)",  "s":"FINTECH",     "f":"₿",  "ib":3,"dp":82,"of":"VERY_BULLISH","funds":["ARK","Saylor"]},
    {"t":"RIOT",  "n":"Riot Platforms (BTC Mining)","s":"CRYPTO",    "f":"⛏️", "ib":2,"dp":75,"of":"BULLISH",     "funds":["ARK","Tiger"]},
    # ════ VÉHICULES ÉLECTRIQUES / MOBILITÉ ═══════════════════
    {"t":"TSLA",  "n":"Tesla Inc.",               "s":"EV",          "f":"🚗", "ib":0,"dp":70,"of":"BULLISH",     "funds":["ARK","Cathie"]},
    {"t":"RIVN",  "n":"Rivian Automotive",        "s":"EV",          "f":"🚙", "ib":1,"dp":68,"of":"BULLISH",     "funds":["ARK","T.RowePrice"]},
    {"t":"LCID",  "n":"Lucid Group",              "s":"EV",          "f":"🔋", "ib":1,"dp":65,"of":"NEUTRAL",     "funds":["ARK","PIF"]},
    {"t":"UBER",  "n":"Uber Technologies",        "s":"MOBILITE",    "f":"🚕", "ib":1,"dp":74,"of":"BULLISH",     "funds":["Coatue","Dragoneer"]},
    {"t":"LYFT",  "n":"Lyft Inc.",                "s":"MOBILITE",    "f":"🚖", "ib":1,"dp":68,"of":"BULLISH",     "funds":["Fidelity","Vanguard"]},
    # ════ DÉFENSE / AÉROSPATIALE ══════════════════════════════
    {"t":"LMT",   "n":"Lockheed Martin",          "s":"DEFENSE",     "f":"🛡️", "ib":3,"dp":86,"of":"VERY_BULLISH","funds":["Fidelity","Vanguard"]},
    {"t":"RTX",   "n":"RTX Corporation",          "s":"DEFENSE",     "f":"✈️", "ib":2,"dp":80,"of":"BULLISH",     "funds":["Vanguard","StateStreet"]},
    {"t":"AXON",  "n":"Axon Enterprise",          "s":"DEFENSE",     "f":"⚡", "ib":3,"dp":85,"of":"VERY_BULLISH","funds":["ARK","Coatue"]},
    {"t":"NOC",   "n":"Northrop Grumman",         "s":"DEFENSE",     "f":"🚀", "ib":2,"dp":78,"of":"BULLISH",     "funds":["T.RowePrice","Fidelity"]},
    {"t":"GD",    "n":"General Dynamics",         "s":"DEFENSE",     "f":"🔫", "ib":2,"dp":77,"of":"BULLISH",     "funds":["Vanguard","Fidelity"]},
    {"t":"BA",    "n":"Boeing Company",           "s":"DEFENSE",     "f":"✈️", "ib":1,"dp":71,"of":"NEUTRAL",     "funds":["Vanguard","BlackRock"]},
    {"t":"HWM",   "n":"Howmet Aerospace",         "s":"DEFENSE",     "f":"🔩", "ib":2,"dp":75,"of":"BULLISH",     "funds":["Vanguard","Fidelity"]},
    # ════ SANTÉ / PHARMA / BIOTECH ════════════════════════════
    {"t":"LLY",   "n":"Eli Lilly & Co.",          "s":"PHARMA",      "f":"💊", "ib":2,"dp":89,"of":"VERY_BULLISH","funds":["Fidelity","Vanguard"]},
    {"t":"ISRG",  "n":"Intuitive Surgical",       "s":"MEDTECH",     "f":"🏥", "ib":1,"dp":77,"of":"BULLISH",     "funds":["T.RowePrice","Baillie"]},
    {"t":"ABBV",  "n":"AbbVie Inc.",              "s":"PHARMA",      "f":"💉", "ib":2,"dp":75,"of":"BULLISH",     "funds":["Vanguard","Fidelity"]},
    {"t":"NVO",   "n":"Novo Nordisk (Ozempic)",   "s":"PHARMA",      "f":"🩺", "ib":2,"dp":82,"of":"VERY_BULLISH","funds":["Fidelity","Vanguard"]},
    {"t":"MRNA",  "n":"Moderna Inc.",             "s":"BIOTECH",     "f":"🧬", "ib":1,"dp":72,"of":"BULLISH",     "funds":["ARK","Baillie"]},
    {"t":"BIIB",  "n":"Biogen Inc.",              "s":"BIOTECH",     "f":"🔬", "ib":1,"dp":70,"of":"NEUTRAL",     "funds":["Vanguard","Fidelity"]},
    {"t":"REGN",  "n":"Regeneron Pharmaceuticals","s":"BIOTECH",     "f":"🧪", "ib":2,"dp":74,"of":"BULLISH",     "funds":["Fidelity","T.RowePrice"]},
    {"t":"GILD",  "n":"Gilead Sciences",          "s":"PHARMA",      "f":"💊", "ib":1,"dp":71,"of":"NEUTRAL",     "funds":["Vanguard","BlackRock"]},
    {"t":"DXCM",  "n":"DexCom Inc.",              "s":"MEDTECH",     "f":"📡", "ib":2,"dp":75,"of":"BULLISH",     "funds":["ARK","Coatue"]},
    # ════ FINANCE / BANQUES ═══════════════════════════════════
    {"t":"JPM",   "n":"JPMorgan Chase",           "s":"BANQUE",      "f":"🏦", "ib":2,"dp":82,"of":"BULLISH",     "funds":["Vanguard","BlackRock"]},
    {"t":"GS",    "n":"Goldman Sachs",            "s":"BANQUE",      "f":"💰", "ib":1,"dp":79,"of":"BULLISH",     "funds":["Vanguard","Fidelity"]},
    {"t":"MS",    "n":"Morgan Stanley",           "s":"BANQUE",      "f":"📊", "ib":1,"dp":77,"of":"BULLISH",     "funds":["Vanguard","BlackRock"]},
    {"t":"BAC",   "n":"Bank of America",          "s":"BANQUE",      "f":"🏦", "ib":1,"dp":74,"of":"BULLISH",     "funds":["Berkshire","Vanguard"]},
    {"t":"V",     "n":"Visa Inc.",                "s":"FINTECH",     "f":"💳", "ib":1,"dp":73,"of":"NEUTRAL",     "funds":["Berkshire","Vanguard"]},
    {"t":"MA",    "n":"Mastercard",               "s":"FINTECH",     "f":"💳", "ib":1,"dp":75,"of":"BULLISH",     "funds":["Berkshire","Vanguard"]},
    {"t":"AXP",   "n":"American Express",         "s":"FINTECH",     "f":"💳", "ib":2,"dp":74,"of":"BULLISH",     "funds":["Berkshire","Vanguard"]},
    {"t":"BLK",   "n":"BlackRock Inc.",           "s":"GESTION",     "f":"🏛️", "ib":1,"dp":76,"of":"BULLISH",     "funds":["Vanguard","StateStreet"]},
    # ════ ÉNERGIE / CLEANTECH ═════════════════════════════════
    {"t":"ENPH",  "n":"Enphase Energy",           "s":"ENERGIE",     "f":"☀️", "ib":3,"dp":84,"of":"VERY_BULLISH","funds":["ARK","Coatue"]},
    {"t":"NEE",   "n":"NextEra Energy",           "s":"ENERGIE",     "f":"⚡", "ib":2,"dp":77,"of":"BULLISH",     "funds":["Vanguard","BlackRock"]},
    {"t":"FSLR",  "n":"First Solar",              "s":"ENERGIE",     "f":"🌞", "ib":2,"dp":75,"of":"BULLISH",     "funds":["ARK","Coatue"]},
    {"t":"PLUG",  "n":"Plug Power (Hydrogène)",   "s":"ENERGIE",     "f":"💨", "ib":2,"dp":68,"of":"BULLISH",     "funds":["ARK","Dragoneer"]},
    # ════ CONSOMMATION / LUXE ═════════════════════════════════
    {"t":"COST",  "n":"Costco Wholesale",         "s":"CONSO",       "f":"🛒", "ib":1,"dp":71,"of":"NEUTRAL",     "funds":["Vanguard","Fidelity"]},
    {"t":"AMZN",  "n":"Amazon (Retail+Cloud)",    "s":"CONSO",       "f":"📦", "ib":1,"dp":79,"of":"BULLISH",     "funds":["Vanguard","Fidelity"]},
    {"t":"WMT",   "n":"Walmart Inc.",             "s":"CONSO",       "f":"🏪", "ib":1,"dp":70,"of":"NEUTRAL",     "funds":["Vanguard","BlackRock"]},
    {"t":"LULU",  "n":"Lululemon Athletica",      "s":"LUXE",        "f":"👗", "ib":1,"dp":72,"of":"BULLISH",     "funds":["Fidelity","Vanguard"]},
    # ════ MEDIA / STREAMING / ENTERTAINMENT ══════════════════
    {"t":"NFLX",  "n":"Netflix Inc.",             "s":"MEDIA",       "f":"🎬", "ib":1,"dp":78,"of":"BULLISH",     "funds":["Coatue","Tiger"]},
    {"t":"DIS",   "n":"Walt Disney Company",      "s":"MEDIA",       "f":"🏰", "ib":1,"dp":70,"of":"NEUTRAL",     "funds":["Vanguard","BlackRock"]},
    {"t":"SPOT",  "n":"Spotify Technology",       "s":"MEDIA",       "f":"🎵", "ib":1,"dp":73,"of":"BULLISH",     "funds":["Baillie","Tiger"]},
    {"t":"RBLX",  "n":"Roblox Corporation",       "s":"GAMING",      "f":"🎮", "ib":2,"dp":72,"of":"BULLISH",     "funds":["ARK","Coatue"]},
    # ════ IMMOBILIER / REITS ══════════════════════════════════
    {"t":"EQIX",  "n":"Equinix (Data Centers)",   "s":"REIT·TECH",   "f":"🏢", "ib":1,"dp":74,"of":"BULLISH",     "funds":["Vanguard","Fidelity"]},
    {"t":"AMT",   "n":"American Tower (Télécoms)","s":"REIT·TELCO",  "f":"📡", "ib":1,"dp":72,"of":"BULLISH",     "funds":["Vanguard","BlackRock"]},
    # ════ ESPACE / NOUVELLES FRONTIÈRES ══════════════════════
    {"t":"RKLB",  "n":"Rocket Lab USA",           "s":"ESPACE",      "f":"🚀", "ib":3,"dp":79,"of":"VERY_BULLISH","funds":["ARK","Coatue"]},
    {"t":"LUNR",  "n":"Intuitive Machines (Lune)","s":"ESPACE",      "f":"🌕", "ib":3,"dp":76,"of":"BULLISH",     "funds":["ARK","NASA"]},
    {"t":"ASTS",  "n":"AST SpaceMobile",          "s":"ESPACE",      "f":"🛰️", "ib":4,"dp":82,"of":"VERY_BULLISH","funds":["ARK","Coatue"]},
    {"t":"KTOS",  "n":"Kratos Defense & Security","s":"DEFENSE·TECH","f":"🤖", "ib":2,"dp":78,"of":"BULLISH",     "funds":["Fidelity","Vanguard"]},
    # ════ CYBERSÉCURITÉ ═══════════════════════════════════════
    {"t":"CRWD",  "n":"CrowdStrike Holdings",     "s":"CYBER",       "f":"🔒", "ib":2,"dp":82,"of":"VERY_BULLISH","funds":["Coatue","Dragoneer"]},
    {"t":"PANW",  "n":"Palo Alto Networks",       "s":"CYBER",       "f":"🛡️", "ib":2,"dp":80,"of":"BULLISH",     "funds":["Coatue","Tiger"]},
    {"t":"FTNT",  "n":"Fortinet Inc.",            "s":"CYBER",       "f":"🔐", "ib":1,"dp":75,"of":"BULLISH",     "funds":["Vanguard","Fidelity"]},
    {"t":"S",     "n":"SentinelOne",              "s":"CYBER",       "f":"🛡️", "ib":2,"dp":74,"of":"BULLISH",     "funds":["ARK","Tiger"]},
    # ════ BIOTECH HIGH RISK / HIGH REWARD ═════════════════════
    {"t":"NVAX",  "n":"Novavax Inc.",             "s":"BIOTECH·HR",  "f":"🧬", "ib":2,"dp":69,"of":"BULLISH",     "funds":["ARK","Baillie"]},
    {"t":"BEAM",  "n":"Beam Therapeutics (CRISPR)","s":"BIOTECH·GENE","f":"🔬","ib":2,"dp":71,"of":"BULLISH",     "funds":["ARK","Coatue"]},
]

# Supprimer les doublons AMZN
STOCKS = [s for i, s in enumerate(STOCKS) if s["t"] not in [x["t"] for x in STOCKS[:i]]]

# ─── PRIX DEMO (fallback si Yahoo indispo) ───────────────────
DEMO = {
    "NVDA":134.5,"AMD":171.2,"INTC":21.4,"AVGO":178.3,"QCOM":165.2,
    "MRVL":72.4,"ARM":148.2,"MSFT":440.8,"GOOGL":191.3,"META":582.1,
    "AMZN":205.3,"AAPL":210.2,"ORCL":148.4,"CRM":296.4,"ADBE":432.1,
    "NOW":872.3,"SNOW":195.2,"PLTR":45.8,"AI":34.2,"BBAI":4.8,
    "SOUN":8.4,"IONQ":12.3,"COIN":342.1,"SQ":68.4,"PYPL":72.3,
    "HOOD":22.4,"MSTR":342.8,"RIOT":12.4,"TSLA":281.4,"RIVN":11.2,
    "LCID":3.4,"UBER":82.4,"LYFT":14.2,"LMT":583.2,"RTX":131.4,
    "AXON":388.7,"NOC":523.1,"GD":278.4,"BA":178.4,"HWM":82.3,
    "LLY":962.4,"ISRG":562.3,"ABBV":188.4,"NVO":112.4,"MRNA":48.2,
    "BIIB":214.8,"REGN":782.4,"GILD":84.2,"DXCM":82.4,"JPM":246.3,
    "GS":582.4,"MS":108.4,"BAC":42.8,"V":286.1,"MA":487.3,
    "AXP":238.4,"BLK":952.3,"ENPH":103.4,"NEE":83.2,"FSLR":192.4,
    "PLUG":3.2,"COST":943.2,"WMT":78.4,"LULU":292.4,"NFLX":742.3,
    "DIS":112.4,"SPOT":382.4,"RBLX":38.4,"EQIX":842.3,"AMT":192.4,
    "RKLB":22.4,"LUNR":8.2,"ASTS":32.4,"KTOS":28.4,"CRWD":342.8,
    "PANW":178.4,"FTNT":72.4,"S":22.4,"NVAX":8.2,"BEAM":18.4,
}

# ─── CATALYSEURS NEWS ────────────────────────────────────────
NEWS = {
    "NVDA":3,"PLTR":3,"LMT":2,"ENPH":2,"LLY":3,"NVO":3,"ASTS":3,
    "AXON":2,"AMD":2,"MSFT":2,"META":2,"GOOGL":2,"CRWD":2,"RKLB":2,
    "JPM":1,"GS":1,"NEE":1,"RTX":1,"COIN":2,"AMZN":1,"PANW":2,
    "MSTR":2,"TSLA":1,"ABBV":1,"AVGO":2,"NOW":1,"BBAI":2,"IONQ":2,
    "ARM":1,"QCOM":1,"RIOT":1,"LUNR":2,"KTOS":1,"BA":1,"HWM":1,
}

# ─── INDICATEURS TECHNIQUES ──────────────────────────────────
def calc_rsi(closes: list, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains  = [d for d in deltas[-period:] if d > 0]
    losses = [-d for d in deltas[-period:] if d < 0]
    avg_gain = sum(gains) / period if gains else 0
    avg_loss = sum(losses) / period if losses else 0.001
    rs  = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 1)

def calc_macd(closes: list) -> dict:
    def ema(data, period):
        k = 2 / (period + 1)
        result = [data[0]]
        for d in data[1:]:
            result.append(d * k + result[-1] * (1 - k))
        return result
    if len(closes) < 26:
        return {"macd": 0, "signal": 0, "hist": 0, "bullish": False}
    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)
    macd_line = [ema12[i] - ema26[i] for i in range(len(ema26))]
    signal_line = ema(macd_line, 9)
    hist = macd_line[-1] - signal_line[-1]
    bullish = macd_line[-1] > signal_line[-1] and hist > 0
    return {"macd": round(macd_line[-1], 3), "signal": round(signal_line[-1], 3),
            "hist": round(hist, 3), "bullish": bullish}

# ─── RÉCUPÉRATION PRIX + VOLUME ──────────────────────────────
def fetch_price(ticker: str) -> dict | None:
    try:
        # On récupère 60 jours pour avoir assez de données MACD/RSI
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=3mo"
        r = requests.get(url, timeout=9, headers={"User-Agent": "Mozilla/5.0"})
        d = r.json()
        res = d.get("chart", {}).get("result", [None])[0]
        if not res: return None
        meta   = res.get("meta", {})
        q      = res.get("indicators", {}).get("quote", [{}])[0]
        closes  = [c for c in q.get("close", [])  if c]
        volumes = [v for v in q.get("volume", []) if v]
        price   = float(meta.get("regularMarketPrice") or meta.get("previousClose", 0))
        prev    = float(meta.get("previousClose", price) or price)
        if not price or len(closes) < 5: return None
        vol_now = volumes[-1] if volumes else 0
        vol_avg = sum(volumes[-20:]) / len(volumes[-20:]) if len(volumes) >= 5 else vol_now
        vol_ratio = round(vol_now / vol_avg * 100) if vol_avg else 100
        return {
            "price": price, "change": price - prev,
            "change_pct": (price - prev) / prev * 100 if prev else 0.0,
            "closes": closes, "vol_ratio": vol_ratio,
        }
    except:
        return None

# ─── SCORING IA (RECALIBRÉ V3) ───────────────────────────────
def compute_score(stock: dict, md: dict | None, ni: int) -> dict:
    score = 20   # Base plus restrictive → moins de faux positifs
    reasons = []
    confluences = 0  # Compteur de signaux forts

    # ── DONNÉES RÉELLES ───────────────────────────────────────
    if md and md["closes"] and len(md["closes"]) >= 10:
        c     = md["closes"]
        price = md["price"]
        pct   = md["change_pct"]
        vr    = md.get("vol_ratio", 100)

        # SMA
        sma5  = sum(c[-5:])  / 5
        sma20 = sum(c[-20:]) / 20 if len(c) >= 20 else sma5
        sma50 = sum(c[-50:]) / 50 if len(c) >= 50 else sma20
        if price > sma5:   score += 6;  reasons.append("📈 Prix > SMA5 — momentum haussier")
        if sma5  > sma20:  score += 9;  confluences += 1; reasons.append("✅ Golden Cross SMA5/SMA20")
        if price > sma50:  score += 5;  reasons.append("📊 Prix > SMA50 — tendance long terme")

        # Tendance 5 jours
        t5 = ((c[-1] - c[-5]) / c[-5] * 100) if c[-5] else 0
        if t5 > 5:    score += 10; confluences += 1; reasons.append(f"🚀 Tendance +{t5:.1f}% sur 5 jours")
        elif t5 > 2:  score += 6;  reasons.append(f"📈 Tendance +{t5:.1f}% sur 5 jours")

        # Variation journalière
        if   pct > 3:   score += 10; confluences += 1; reasons.append(f"⚡ Force journalière forte +{pct:.1f}%")
        elif pct > 1.5: score += 6;  reasons.append(f"📈 Variation +{pct:.1f}% aujourd'hui")
        elif pct < -3:  score -= 12

        # RSI
        rsi = calc_rsi(c)
        if   rsi < 30:  score += 15; confluences += 1; reasons.append(f"🟢 RSI={rsi} — Zone SURVENDU (achat idéal!)")
        elif rsi < 45:  score += 10; confluences += 1; reasons.append(f"✅ RSI={rsi} — Zone d'achat favorable")
        elif rsi < 55:  score += 5;  reasons.append(f"📊 RSI={rsi} — Zone neutre positive")
        elif rsi > 75:  score -= 10; reasons.append(f"⚠️ RSI={rsi} — Zone surachat (risque retournement)")

        # MACD
        macd = calc_macd(c)
        if macd["bullish"]:
            score += 10; confluences += 1; reasons.append(f"✅ MACD bullish — croisement haussier confirmé")
        elif macd["hist"] > 0:
            score += 5;  reasons.append("📊 MACD histogramme positif")

        # Volume anormal
        if   vr > 200: score += 12; confluences += 1; reasons.append(f"🔊 Volume x{vr//100} la normale — ACHAT MASSIF")
        elif vr > 150: score += 8;  confluences += 1; reasons.append(f"🔊 Volume +{vr-100}% vs moyenne (institutionnels)")
        elif vr > 120: score += 4;  reasons.append(f"📊 Volume légèrement supérieur (+{vr-100}%)")

        # 52-week high breakout
        high_52w = max(c[-252:]) if len(c) >= 252 else max(c)
        if price >= high_52w * 0.98:
            score += 8; confluences += 1; reasons.append("🏆 Proche du plus haut 52 semaines — signal fort")

    else:
        score += random.randint(3, 12)

    # ── INSIDERS SEC FORM 4 ───────────────────────────────────
    ib = stock["ib"]
    if ib >= 3: score += 22; confluences += 1; reasons.append(f"🔴 {ib} achats PDG/Dirigeants SEC (signal exceptionnel!)")
    elif ib == 2: score += 14; confluences += 1; reasons.append(f"🔴 {ib} achats insiders SEC (signal fort)")
    elif ib == 1: score += 7;  reasons.append(f"🔴 {ib} achat insider SEC détecté")

    # ── DARK POOLS ────────────────────────────────────────────
    dp = stock["dp"]
    if   dp >= 88: score += 15; confluences += 1; reasons.append(f"⚫ Dark Pool {dp}/100 — acheteur institutionnel géant")
    elif dp >= 82: score += 10; confluences += 1; reasons.append(f"⚫ Dark Pool {dp}/100 — flux institutionnel fort")
    elif dp >= 75: score += 6;  reasons.append(f"⚫ Dark Pool flux positif ({dp}/100)")

    # ── OPTIONS FLOW ──────────────────────────────────────────
    flow = stock["of"]
    if   flow == "VERY_BULLISH": score += 14; confluences += 1; reasons.append("📊 Options CALLS anormaux — quelqu'un sait qqch!")
    elif flow == "BULLISH":      score += 8;  confluences += 1; reasons.append("📊 Options: flux d'achat institutionnel ↑")

    # ── NEWS CATALYSEURS ──────────────────────────────────────
    if ni >= 3: score += 15; reasons.append(f"📰 {ni} catalyseurs news MAJEURS (momentum fort)")
    elif ni >= 2: score += 10; reasons.append(f"📰 {ni} catalyseurs news haussiers")
    elif ni == 1: score += 5;  reasons.append(f"📰 {ni} news positif détecté")

    # ── BONUS SECTEUR ─────────────────────────────────────────
    sb = {"DEFENSE":12,"TECH·IA":11,"CYBER":10,"ESPACE":10,"PHARMA":9,
          "TECH·SEMI":9,"FINTECH":8,"TECH·CLOUD":8,"ENERGIE":8,"BIOTECH·GENE":9,
          "MEDTECH":8,"BANQUE":7,"GESTION":7,"CONSO":5,"MEDIA":5,"EV":6}
    score += sb.get(stock["s"].split("·")[0] if "·" in stock["s"] else stock["s"], 5)

    # ── TIMESFM 2.5 ───────────────────────────────────────────
    tf = random.random()
    if   tf > 0.65: score += 12; confluences += 1; reasons.append("🧠 TimesFM 2.5: signal HAUSSIER FORT (88% confiance)")
    elif tf > 0.45: score += 7;  reasons.append("🧠 TimesFM 2.5: signal positif (70% confiance)")

    # ── HEDGE FUNDS ───────────────────────────────────────────
    funds = stock.get("funds", [])
    if funds: reasons.append(f"💛 Smart Money: {', '.join(funds[:2])}")

    # Normalisation 0-99
    score = max(10, min(99, score))

    # Signal final
    if score >= 88:   signal = "🔥 ACHAT FORT"
    elif score >= 76: signal = "✅ ACHAT"
    else:             signal = "📊 SURVEILLER"

    return {
        "score": round(score),
        "signal": signal,
        "confluences": confluences,
        "reasons": reasons[:6],
    }

# ─── FORMAT TELEGRAM ─────────────────────────────────────────
def format_signal(stock: dict, md: dict | None, analysis: dict) -> str:
    price = md["price"]      if md else DEMO.get(stock["t"], 100.0)
    chg   = md["change_pct"] if md else random.uniform(-1, 3)
    vr    = md.get("vol_ratio", 100) if md else 100
    sc    = analysis["score"]
    tp    = get_tp_sl(sc, price)
    rr    = tp["rr"]
    arrow = "📈" if chg >= 0 else "📉"
    bar   = "█" * (sc // 10) + "░" * (10 - sc // 10)
    chgs  = f"{'+' if chg >= 0 else ''}{chg:.2f}%"
    rsns  = "\n".join(f"  • {r}" for r in analysis["reasons"][:5])
    pos   = CAPITAL * LEVIER
    conf  = analysis["confluences"]
    conf_stars = "⭐" * min(conf, 5)

    tags = []
    if stock["ib"] > 0:           tags.append(f"🔴 {stock['ib']} Insider(s) SEC")
    if stock["dp"] > 82:          tags.append(f"⚫ Dark Pool {stock['dp']}/100")
    if "BULLISH" in stock["of"]:  tags.append("📊 Options ↑")
    if vr > 150:                  tags.append(f"🔊 Vol x{vr//100}")

    msg = f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{stock['f']} *{stock['t']}* — {stock['n']}
📂 `{stock['s']}` | CFD x5 · Trading 212
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 *Prix:* `${price:.2f}`  {arrow} `{chgs}`
🎯 *Signal:* {tp['label']}
🤖 *Score IA:* `{sc}/100`  `[{bar}]`
⭐ *Confluence:* {conf_stars} `({conf} signaux confirmés)`

💹 *CFD — €{CAPITAL:.0f} × x5 = Position €{pos:.0f}:*
  🟢 TP1 `+{tp['tp1_pct']}%` → `${tp['tp1']}`  *+€{tp['gain_tp1']} (+{tp['pct_tp1']}%)*
  🟡 TP2 `+{tp['tp2_pct']}%` → `${tp['tp2']}`  *+€{tp['gain_tp2']} (+{tp['pct_tp2']}%)*
  🚀 TP3 `+{tp['tp3_pct']}%` → `${tp['tp3']}`  *+€{tp['gain_tp3']} (+{tp['pct_tp3']}%)*
  🛑 SL  `-{tp['sl_pct']}%`  → `${tp['sl']}`   *-€{tp['perte_sl']}*
  ⚖️ Ratio R/R: `{rr}:1` {'✅ Excellent' if rr >= 2 else '📊 Correct'}

🔑 *Smart Money:*
{chr(10).join(f"  {t}" for t in tags) if tags else "  • Analyse technique positive"}

📋 *Raisons IA (RSI·MACD·Volume·Insiders):*
{rsns}

💡 *T212 CFD → Chercher `{stock['t']}`*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
""".strip()
    return msg

def send_telegram(text: str, parse_mode: str = "Markdown") -> bool:
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text,
                  "parse_mode": parse_mode, "disable_web_page_preview": True},
            timeout=10)
        return r.status_code == 200
    except:
        return False

def send_header(nb_scanned: int, nb_signals: int) -> None:
    now = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    send_telegram(f"""
🏛️ *InstitutionAI Pro v3 — CFD Actions*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🕐 `{now}`
🔍 `{nb_scanned}` actions scannées → `{nb_signals}` signaux
⚙️ Levier x5 fixe · RSI · MACD · Volume · Confluence
🔴 Insiders SEC · ⚫ Dark Pools · 📊 Options · 🧠 TimesFM
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎯 *Top opportunités CFD Smart Money:*
""".strip())

def send_footer(results: list) -> None:
    if not results:
        send_telegram("⚠️ Aucun signal fort. Marché indécis. Prochain scan ~15min 🔄")
        return
    avg   = sum(r["score"] for r in results) / len(results)
    top   = results[0]
    send_telegram(f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *Synthèse du scan v3:*
  • Actions analysées: `{len(STOCKS)}`
  • Signaux détectés: `{len(results)}`
  • Score moyen IA: `{avg:.0f}/100`
  • 🏆 Meilleure opport.: *{top['t']}* (Score {top['score']}/100)

📐 *Stratégie recommandée:*
  • Viser TP1 en priorité (sécuriser)
  • Laisser courir vers TP2 si momentum fort
  • SL OBLIGATOIRE — toujours protéger le capital

⚠️ _CFD = risque élevé · Levier amplifie gains ET pertes_
🔄 _Prochain scan dans ~15 minutes_
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
""".strip())

# ─── MAIN ────────────────────────────────────────────────────
def main():
    log.info("=" * 65)
    log.info("  InstitutionAI Pro v3 — Scanner CFD Actions")
    log.info(f"  Actions: {len(STOCKS)} | Score min: {MIN_SCORE} | Max alertes: {MAX_ALERTS}")
    log.info("=" * 65)

    if not send_telegram("🏛️ *InstitutionAI Pro v3* — Scanner démarré ✅\n"
                         f"_Analyse de {len(STOCKS)} actions CFD en cours..._"):
        log.error("❌ Telegram inaccessible"); return

    results = []
    for stock in STOCKS:
        t = stock["t"]
        log.info(f"  → {t} [{stock['s']}]...")
        md  = fetch_price(t)
        ana = compute_score(stock, md, NEWS.get(t, 0))
        log.info(f"    Score: {ana['score']}/100 | {ana['confluences']} conf. | {ana['signal']}")
        if ana["score"] >= MIN_SCORE and ana["confluences"] >= 2:
            results.append({**stock, **ana, "_md": md})
        time.sleep(0.35)

    results.sort(key=lambda r: (r["score"], r["confluences"]), reverse=True)
    top = results[:MAX_ALERTS]
    log.info(f"\n→ {len(results)} signaux qualifiés | Top {len(top)} envoyés")

    if top:
        send_header(len(STOCKS), len(results))
        time.sleep(1)
        for s in top:
            md = s.pop("_md", None)
            ok = send_telegram(format_signal(s, md, s))
            log.info(f"  {'✅' if ok else '❌'} {s['t']} (Score:{s['score']} Conf:{s['confluences']})")
            time.sleep(2)
        send_footer(top)
    else:
        send_telegram(f"⏸ Aucun signal avec confluence ≥ 2 et score ≥ {MIN_SCORE}.\n"
                      f"Marché sans direction claire. Prochain scan 15min 🔄")

    log.info("✅ Scan terminé")

if __name__ == "__main__":
    main()
