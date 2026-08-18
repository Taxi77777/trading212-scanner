from __future__ import annotations

import requests

FRANKFURTER_URL = "https://api.frankfurter.dev/v2"
SOURCE_NAME = "Yahoo Finance intraday + Frankfurter/ECB reference + Forex Factory calendar"


def reference_rate(base: str, quote: str) -> float | None:
    """Return a free ECB-backed reference rate via Frankfurter, when available."""
    try:
        r = requests.get(
            f"{FRANKFURTER_URL}/rate/{base}/{quote}",
            params={"providers": "ECB"},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        return float(data["rate"])
    except Exception:
        return None
