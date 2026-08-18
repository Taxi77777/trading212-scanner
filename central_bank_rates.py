from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Dict, Tuple

import requests

CACHE = Path("central_bank_rates.json")
TIMEOUT = 12
HEADERS = {"User-Agent": "Mozilla/5.0 T212ForexRates/1.0", "Accept": "text/html,application/xhtml+xml"}
SOURCES = [
    "https://www.forex.pm/central-bank-rates",
    "https://www.investing.com/central-banks/",
]
PATTERNS = {
    "USD": r"(?:Federal Reserve|FED).*?USD.*?([0-9]+(?:\.[0-9]+)?)%",
    "EUR": r"(?:European Central Bank|ECB).*?EUR.*?([0-9]+(?:\.[0-9]+)?)%",
    "GBP": r"(?:Bank of England|BOE).*?GBP.*?([0-9]+(?:\.[0-9]+)?)%",
    "JPY": r"(?:Bank of Japan|BOJ).*?JPY.*?([0-9]+(?:\.[0-9]+)?)%",
    "CHF": r"(?:Swiss National Bank|SNB).*?CHF.*?([0-9]+(?:\.[0-9]+)?)%",
    "AUD": r"(?:Reserve Bank of Australia|RBA).*?AUD.*?([0-9]+(?:\.[0-9]+)?)%",
    "CAD": r"(?:Bank of Canada|BOC).*?CAD.*?([0-9]+(?:\.[0-9]+)?)%",
    "NZD": r"(?:Reserve Bank of New Zealand|RBNZ).*?NZD.*?([0-9]+(?:\.[0-9]+)?)%",
}


def _clean(text: str) -> str:
    text = re.sub(r"<script.*?</script>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text)


def fetch_rates() -> Tuple[Dict[str, float], str]:
    for url in SOURCES:
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            text = _clean(r.text)
            rates = {}
            for ccy, pattern in PATTERNS.items():
                m = re.search(pattern, text, re.I)
                if m:
                    rates[ccy] = float(m.group(1))
            if len(rates) >= 6:
                payload = {"rates": rates, "source": url, "fetched_at": time.time()}
                CACHE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                return rates, url
        except Exception:
            continue
    try:
        payload = json.loads(CACHE.read_text(encoding="utf-8"))
        return payload.get("rates", {}), payload.get("source", "cache")
    except Exception:
        return {}, "unavailable"


def load_rates() -> Tuple[Dict[str, float], str]:
    return fetch_rates()


def differential(base: str, quote: str, rates: Dict[str, float]) -> float | None:
    if base not in rates or quote not in rates:
        return None
    return rates[base] - rates[quote]


def assessment(pair: str, side: str, rates: Dict[str, float]) -> tuple[str, float | None]:
    base, quote = pair.split("/")
    diff = differential(base, quote, rates)
    if diff is None:
        return "TAUX_INCONNU", None
    signed = diff if side.upper() == "BUY" else -diff
    if signed >= 1.00:
        return "TAUX_FORTEMENT_FAVORABLE", diff
    if signed >= 0.25:
        return "TAUX_FAVORABLE", diff
    if signed <= -1.00:
        return "TAUX_FORTEMENT_CONTRE", diff
    if signed <= -0.25:
        return "TAUX_CONTRE", diff
    return "TAUX_NEUTRE", diff
