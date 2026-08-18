from __future__ import annotations

from datetime import datetime, timezone
import requests

# Completely free, no-key market/reference sources.
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart"
FRANKFURTER_URL = "https://api.frankfurter.dev/v2/rates"


def source_status() -> dict[str, str]:
    """Return availability of the free reference sources without requiring keys."""
    out = {"intraday": "Yahoo Finance", "reference_fx": "Frankfurter/ECB", "calendar": "Forex Factory JSON"}
    try:
        r = requests.get(f"{YAHOO_URL}/EURUSD=X", params={"range": "1d", "interval": "15m", "includePrePost": "false"}, timeout=8)
        r.raise_for_status()
        result = r.json().get("chart", {}).get("result")
        if not result:
            out["intraday"] = "Yahoo Finance indisponible"
    except Exception:
        out["intraday"] = "Yahoo Finance indisponible"

    try:
        r = requests.get(FRANKFURTER_URL, params={"base": "EUR", "symbols": "USD,CHF,GBP,JPY,CAD,AUD,NZD"}, timeout=8)
        r.raise_for_status()
        if not isinstance(r.json(), list):
            out["reference_fx"] = "Frankfurter/ECB indisponible"
    except Exception:
        out["reference_fx"] = "Frankfurter/ECB indisponible"
    return out


def frankfurter_reference(base: str, symbols: str) -> list[dict]:
    """Free ECB reference rates; daily/reference data, not an intraday feed."""
    try:
        r = requests.get(FRANKFURTER_URL, params={"base": base, "symbols": symbols}, timeout=10)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []
    except Exception:
        return []
