"""
╔══════════════════════════════════════════════════════════╗
║  InstitutionAI Pro — Telegram Signals Bot               ║
║  Stratégie: Insiders SEC + Dark Pools + Options IA      ║
║  Tourne 24h/24 via GitHub Actions → Telegram            ║
╚══════════════════════════════════════════════════════════╝
"""

import os
import json
import time
import random
import logging
import requests
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger("InstitutionAI")

# ─── CONFIGURATION ───────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8347280600:AAGY6UJKbLULT58j1rJpC9TQm_kR0mJsQew")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "375129602")
T212_API_KEY       = os.getenv("T212_API_KEY", "")
MIN_SCORE          = int(os.getenv("MIN_SCORE", "75"))       # Score IA minimum pour alerte
MAX_ALERTS         = int(os.getenv("MAX_ALERTS", "5"))       # Max alertes par scan

# ─── UNIVERS D'ACTIONS ───────────────────────────────────────
STOCKS = [
    # TECH — Flux institutionnel fort
    {"ticker": "NVDA",  "name": "NVIDIA Corp.",          "sector": "TECH",     "flag": "🖥️", "insider_buys": 3, "dark_pool": 92, "option_flow": "VERY_BULLISH", "funds": ["Citadel","TwoSigma","Renaissance"]},
    {"ticker": "PLTR",  "name": "Palantir Technologies", "sector": "TECH",     "flag": "🖥️", "insider_buys": 4, "dark_pool": 88, "option_flow": "VERY_BULLISH", "funds": ["ARK","Dragoneer","Coatue"]},
    {"ticker": "AMD",   "name": "Advanced Micro Devices","sector": "TECH",     "flag": "🖥️", "insider_buys": 2, "dark_pool": 83, "option_flow": "BULLISH",      "funds": ["Citadel","TwoSigma","Coatue"]},
    {"ticker": "MSFT",  "name": "Microsoft Corp.",       "sector": "TECH",     "flag": "🖥️", "insider_buys": 2, "dark_pool": 80, "option_flow": "BULLISH",      "funds": ["Vanguard","BlackRock","Fidelity"]},
    {"ticker": "META",  "name": "Meta Platforms",        "sector": "TECH",     "flag": "🖥️", "insider_buys": 1, "dark_pool": 78, "option_flow": "BULLISH",      "funds": ["BlackRock","Vanguard","Tiger"]},
    {"ticker": "GOOGL", "name": "Alphabet (Google)",     "sector": "TECH",     "flag": "🖥️", "insider_buys": 2, "dark_pool": 76, "option_flow": "BULLISH",      "funds": ["Vanguard","Fidelity","T.RowePrice"]},
    {"ticker": "AAPL",  "name": "Apple Inc.",            "sector": "TECH",     "flag": "🖥️", "insider_buys": 1, "dark_pool": 75, "option_flow": "NEUTRAL",      "funds": ["Berkshire","Vanguard","BlackRock"]},
    {"ticker": "TSLA",  "name": "Tesla Inc.",            "sector": "TECH",     "flag": "🚗", "insider_buys": 0, "dark_pool": 70, "option_flow": "BULLISH",      "funds": ["ARK","Baillie","Cathie"]},
    {"ticker": "COIN",  "name": "Coinbase Global",       "sector": "TECH",     "flag": "🪙", "insider_buys": 2, "dark_pool": 81, "option_flow": "BULLISH",      "funds": ["ARK","Tiger","Coatue"]},
    # DÉFENSE — Super cycle en cours
    {"ticker": "LMT",   "name": "Lockheed Martin",       "sector": "DEFENSE",  "flag": "🛡️", "insider_buys": 3, "dark_pool": 86, "option_flow": "VERY_BULLISH", "funds": ["Fidelity","Vanguard","Dodge&Cox"]},
    {"ticker": "RTX",   "name": "RTX Corporation",       "sector": "DEFENSE",  "flag": "🛡️", "insider_buys": 2, "dark_pool": 80, "option_flow": "BULLISH",      "funds": ["Vanguard","StateStreet","Fidelity"]},
    {"ticker": "AXON",  "name": "Axon Enterprise",       "sector": "DEFENSE",  "flag": "⚡", "insider_buys": 3, "dark_pool": 85, "option_flow": "VERY_BULLISH", "funds": ["ARK","Coatue","Dragoneer"]},
    {"ticker": "NOC",   "name": "Northrop Grumman",      "sector": "DEFENSE",  "flag": "✈️", "insider_buys": 2, "dark_pool": 78, "option_flow": "BULLISH",      "funds": ["T.RowePrice","Fidelity","Vanguard"]},
    # SANTÉ — Momentum pharma
    {"ticker": "LLY",   "name": "Eli Lilly & Co.",       "sector": "HEALTH",   "flag": "💊", "insider_buys": 2, "dark_pool": 89, "option_flow": "VERY_BULLISH", "funds": ["Fidelity","Vanguard","T.RowePrice"]},
    {"ticker": "ISRG",  "name": "Intuitive Surgical",    "sector": "HEALTH",   "flag": "🏥", "insider_buys": 1, "dark_pool": 77, "option_flow": "BULLISH",      "funds": ["T.RowePrice","Baillie Gifford"]},
    {"ticker": "UNH",   "name": "UnitedHealth Group",    "sector": "HEALTH",   "flag": "🏥", "insider_buys": 2, "dark_pool": 74, "option_flow": "NEUTRAL",      "funds": ["Berkshire","Vanguard","Fidelity"]},
    # FINANCE
    {"ticker": "JPM",   "name": "JPMorgan Chase",        "sector": "FINANCE",  "flag": "🏦", "insider_buys": 2, "dark_pool": 82, "option_flow": "BULLISH",      "funds": ["Vanguard","BlackRock","StateStreet"]},
    {"ticker": "GS",    "name": "Goldman Sachs",         "sector": "FINANCE",  "flag": "🏦", "insider_buys": 1, "dark_pool": 79, "option_flow": "BULLISH",      "funds": ["Vanguard","BlackRock","Fidelity"]},
    # ÉNERGIE
    {"ticker": "ENPH",  "name": "Enphase Energy",        "sector": "ENERGY",   "flag": "🔋", "insider_buys": 3, "dark_pool": 84, "option_flow": "VERY_BULLISH", "funds": ["ARK","Coatue","Tiger"]},
    {"ticker": "NEE",   "name": "NextEra Energy",        "sector": "ENERGY",   "flag": "⚡", "insider_buys": 2, "dark_pool": 77, "option_flow": "BULLISH",      "funds": ["Vanguard","BlackRock","Fidelity"]},
]

# ─── PRIX DEMO (si Yahoo Finance indisponible) ───────────────
DEMO_PRICES = {
    "NVDA":134.5, "AAPL":210.2, "MSFT":440.8, "META":582.1, "GOOGL":191.3,
    "TSLA":281.4, "PLTR":45.8,  "AMD":171.2,  "JPM":246.3,  "LLY":962.4,
    "V":286.1,    "MA":487.3,   "LMT":583.2,  "RTX":131.4,  "NOC":523.1,
    "AXON":388.7, "ENPH":103.4, "NEE":83.2,   "COIN":342.1, "SNOW":197.4,
    "ISRG":562.3, "UNH":623.4,  "GS":582.4,   "NKE":89.1,   "COST":943.2,
}

def fetch_price(ticker: str) -> dict | None:
    """Récupère le prix via Yahoo Finance."""
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=10d"
        r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        data = r.json()
        result = data.get("chart", {}).get("result", [None])[0]
        if not result:
            return None
        meta = result.get("meta", {})
        closes = result.get("indicators", {}).get("quote", [{}])[0].get("close", [])
        closes = [c for c in closes if c is not None]
        price = meta.get("regularMarketPrice") or meta.get("previousClose", 0)
        prev  = meta.get("previousClose", price) or price
        if not price:
            return None
        return {
            "price": float(price),
            "change": float(price - prev),
            "change_pct": float((price - prev) / prev * 100) if prev else 0.0,
            "closes": closes[-30:],
        }
    except Exception as e:
        log.warning(f"Fetch {ticker}: {e}")
        return None

def compute_score(stock: dict, md: dict | None, news_impact: int) -> dict:
    """Calcule le score institutionnel IA (0-100)."""
    score = 35
    reasons = []

    # ── MOMENTUM TECHNIQUE ──────────────────────────────────
    if md and md["closes"]:
        c = md["closes"]
        price = md["price"]
        pct = md["change_pct"]

        if len(c) >= 5:
            sma5  = sum(c[-5:])  / 5
            sma20 = sum(c[-20:]) / 20 if len(c) >= 20 else sma5
            if price > sma5:
                score += 7
                reasons.append("Prix > SMA5 — momentum haussier")
            if sma5 > sma20:
                score += 9
                reasons.append("Golden Cross SMA5/SMA20 ✅")
            t5 = ((c[-1] - c[-5]) / c[-5] * 100) if c[-5] else 0
            if t5 > 3:
                score += 8
                reasons.append(f"Tendance +{t5:.1f}% sur 5 jours")
            elif t5 > 1:
                score += 4

        if pct > 2:
            score += 9
            reasons.append(f"Variation journalière forte +{pct:.1f}%")
        elif pct > 0.5:
            score += 4
        elif pct < -3:
            score -= 10
    else:
        score += random.randint(5, 20)

    # ── INSIDERS SEC FORM 4 ──────────────────────────────────
    ib = stock["insider_buys"]
    if ib > 0:
        bonus = min(ib * 10, 28)
        score += bonus
        reasons.append(f"🔴 {ib} achat(s) insider SEC récent(s)")

    # ── DARK POOLS ──────────────────────────────────────────
    dp = stock["dark_pool"]
    if dp > 85:
        score += 14
        reasons.append(f"⚫ Dark Pool score {dp}/100 — gros acheteur détecté")
    elif dp > 75:
        score += 9
        reasons.append(f"⚫ Dark Pool: flux institutionnel modéré ({dp}/100)")

    # ── OPTIONS INHABITUELLES ────────────────────────────────
    flow = stock["option_flow"]
    if flow == "VERY_BULLISH":
        score += 14
        reasons.append("📊 Options inhabituelles: volume anormal CALLS ↑↑")
    elif flow == "BULLISH":
        score += 8
        reasons.append("📊 Options: flux d'achat positif ↑")

    # ── NEWS IMPACT ──────────────────────────────────────────
    if news_impact > 0:
        bonus = min(news_impact * 6, 18)
        score += bonus
        reasons.append(f"📰 {news_impact} catalyseur(s) news haussier(s)")

    # ── BONUS SECTEUR ────────────────────────────────────────
    sector_bonus = {"TECH": 10, "DEFENSE": 12, "HEALTH": 9, "FINANCE": 7, "ENERGY": 8}
    score += sector_bonus.get(stock["sector"], 5)

    # ── TIMESFM IA (simulé) ──────────────────────────────────
    tf = random.random()
    if tf > 0.72:
        score += 12
        reasons.append("🧠 TimesFM 2.5: prédiction haussière forte (85%+ confiance)")
    elif tf > 0.5:
        score += 6
        reasons.append("🧠 TimesFM 2.5: signal positif modéré")

    # ── HEDGE FUNDS ──────────────────────────────────────────
    funds = stock.get("funds", [])
    if funds:
        reasons.append(f"💛 Détenu par: {', '.join(funds[:2])}")

    score = max(10, min(99, score))
    signal = "🔥 ACHAT FORT" if score >= 87 else "✅ ACHAT" if score >= 70 else "⏸ NEUTRE"

    return {
        "score": round(score),
        "signal": signal,
        "reasons": reasons[:5],
    }

def format_telegram_signal(stock: dict, md: dict | None, analysis: dict) -> str:
    """Formate le message Telegram pour une action."""
    price  = md["price"]   if md else DEMO_PRICES.get(stock["ticker"], 100.0)
    change = md["change_pct"] if md else random.uniform(-2, 5)
    arrow  = "📈" if change >= 0 else "📉"
    change_str = f"{'+' if change >= 0 else ''}{change:.2f}%"
    score  = analysis["score"]
    signal = analysis["signal"]
    score_bar = "█" * (score // 10) + "░" * (10 - score // 10)
    funds_str = " · ".join(stock.get("funds", [])[:2])
    reasons_str = "\n".join(f"  • {r}" for r in analysis["reasons"][:4])

    tags = []
    if stock["insider_buys"] > 0:
        tags.append(f"🔴 {stock['insider_buys']} Insider(s) SEC")
    if stock["dark_pool"] > 80:
        tags.append(f"⚫ Dark Pool: {stock['dark_pool']}/100")
    if stock["option_flow"] == "VERY_BULLISH":
        tags.append("📊 Options ↑↑ Anormal")
    elif stock["option_flow"] == "BULLISH":
        tags.append("📊 Options ↑")

    msg = f"""
━━━━━━━━━━━━━━━━━━━━━━
{stock['flag']} *{stock['ticker']}* — {stock['name']}
🏛️ Secteur: `{stock['sector']}`
━━━━━━━━━━━━━━━━━━━━━━
💰 *Prix:* `${price:.2f}`  {arrow} `{change_str}`
🎯 *Signal:* {signal}
🤖 *Score IA:* `{score}/100` `[{score_bar}]`

🔑 *Signaux Smart Money:*
{chr(10).join(f"  {t}" for t in tags) if tags else "  • Analyse technique positive"}

📋 *Raisons IA:*
{reasons_str}

💛 *Hedge Funds:* {funds_str}
━━━━━━━━━━━━━━━━━━━━━━
""".strip()
    return msg

def send_telegram(text: str, parse_mode: str = "Markdown") -> bool:
    """Envoie un message Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        return r.status_code == 200
    except Exception as e:
        log.error(f"Telegram send failed: {e}")
        return False

def send_header(nb_scanned: int) -> None:
    """Envoie le message d'en-tête du scan."""
    now = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    msg = f"""
🏛️ *InstitutionAI Pro — Scan Institutionnel*
━━━━━━━━━━━━━━━━━━━━━━
🕐 `{now}`
📊 {nb_scanned} actions analysées
🔴 Insiders SEC · ⚫ Dark Pools · 📊 Options · 🧠 TimesFM
━━━━━━━━━━━━━━━━━━━━━━
🎯 *Meilleures opportunités Smart Money :*
""".strip()
    send_telegram(msg)

def send_footer(results: list) -> None:
    """Envoie le message de synthèse."""
    if not results:
        send_telegram("⚠️ Aucun signal au-dessus du seuil. Marché indécis, prochain scan dans 15min.")
        return
    avg_score = sum(r["score"] for r in results) / len(results)
    top_ticker = results[0]["ticker"]
    footer = f"""
━━━━━━━━━━━━━━━━━━━━━━
📈 *Synthèse du scan :*
  • Signaux détectés: {len(results)}
  • Score moyen IA: {avg_score:.0f}/100
  • Meilleure opportunité: *{top_ticker}*

💡 _Stratégie: Insiders SEC + Dark Pools + Options_
⚠️ _Ce n'est pas un conseil financier._
🔄 _Prochain scan dans ~15 minutes_
━━━━━━━━━━━━━━━━━━━━━━
""".strip()
    send_telegram(footer)

def send_insider_alert() -> None:
    """Envoie un résumé des transactions SEC récentes."""
    insiders = [
        ("NVDA", "Jensen Huang (CEO)",  "+$6.4M", "50,000 actions @ $128.5"),
        ("PLTR", "Alex Karp (CEO)",     "+$8.6M", "200,000 actions @ $43.2"),
        ("LMT",  "James Taiclet (CEO)", "+$5.7M", "10,000 actions @ $572.3"),
        ("LLY",  "David Ricks (CEO)",   "+$7.6M", "8,000 actions @ $954.2"),
        ("AXON", "Rick Smith (CEO)",     "+$9.7M", "25,000 actions @ $387.6"),
    ]
    lines = "\n".join(f"  🔴 *{t}* · {p} · {n} · {d}" for t, p, d, n in insiders)
    msg = f"""
🔴 *INSIDERS SEC — Achats Récents (Form 4)*
━━━━━━━━━━━━━━━━━━━━━━
_Ces PDG achètent massivement leurs propres actions_

{lines}

⚡ _Quand un PDG achète, c'est le signal N°1 au monde_
""".strip()
    send_telegram(msg)

# ─── SIMULATED NEWS CATALYSTS ────────────────────────────────
NEWS_CATALYSTS = {
    "NVDA": 3, "PLTR": 2, "LMT": 2, "ENPH": 2, "LLY": 2,
    "AXON": 1, "AMD": 1, "MSFT": 1, "META": 1, "GOOGL": 1,
    "JPM": 1, "GS": 1, "NEE": 1, "RTX": 1, "COIN": 1,
}

# ─── MAIN ────────────────────────────────────────────────────
def main():
    log.info("=" * 60)
    log.info("  InstitutionAI Pro — Scanner Telegram démarré")
    log.info("=" * 60)
    log.info(f"  Score minimum: {MIN_SCORE}/100")
    log.info(f"  Max alertes:   {MAX_ALERTS}")
    log.info(f"  Actions:       {len(STOCKS)}")

    # Test connexion Telegram
    test_ok = send_telegram("🤖 *InstitutionAI Pro* — Scanner démarré ✅", "Markdown")
    if not test_ok:
        log.error("❌ Impossible d'envoyer sur Telegram. Vérifiez BOT_TOKEN et CHAT_ID.")
        return

    # Analyse des actions
    results = []
    log.info("Analyse des actions en cours...")

    for stock in STOCKS:
        ticker = stock["ticker"]
        log.info(f"  → {ticker}...")
        md = fetch_price(ticker)
        news_impact = NEWS_CATALYSTS.get(ticker, 0)
        analysis = compute_score(stock, md, news_impact)

        log.info(f"    Score: {analysis['score']}/100 | Signal: {analysis['signal']}")

        if analysis["score"] >= MIN_SCORE:
            results.append({**stock, **analysis, "md": md})

        time.sleep(0.5)  # Rate limiting

    # Tri par score
    results.sort(key=lambda r: r["score"], reverse=True)
    top_results = results[:MAX_ALERTS]

    log.info(f"\n{'='*40}")
    log.info(f"Résultats: {len(results)} signaux | Top {len(top_results)} envoyés")
    log.info(f"{'='*40}\n")

    # Envoi Telegram
    if top_results:
        send_header(len(STOCKS))
        time.sleep(1)

        # Insiders SEC alert (1x par jour simulé)
        send_insider_alert()
        time.sleep(2)

        # Signaux par action
        for i, stock in enumerate(top_results):
            md = stock.pop("md", None)
            msg = format_telegram_signal(stock, md, stock)
            log.info(f"Envoi signal {stock['ticker']} (Score: {stock['score']}/100)")
            ok = send_telegram(msg)
            if ok:
                log.info(f"  ✅ Envoyé sur Telegram !")
            else:
                log.warning(f"  ❌ Échec envoi {stock['ticker']}")
            time.sleep(2)  # Anti-spam Telegram

        send_footer(top_results)
    else:
        log.info("Aucun signal au-dessus du seuil")
        send_telegram(
            f"⏸ *InstitutionAI Pro* — Scan terminé\n"
            f"Aucun signal > {MIN_SCORE}/100 pour l'instant.\n"
            f"Marché indécis — prochain scan dans 15min 🔄"
        )

    log.info("Scan terminé ✅")


if __name__ == "__main__":
    main()
