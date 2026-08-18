from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Callable

import requests

TD_URL = "https://api.twelvedata.com/time_series"
API_KEY = os.getenv("TWELVE_DATA_API_KEY", "").strip()

# Yahoo symbol -> Twelve Data forex symbol. Macro symbols intentionally remain
# on Yahoo in the scanner because they are not equivalent currency instruments.
FOREX_SYMBOLS = {
    "EURUSD=X": "EUR/USD", "GBPUSD=X": "GBP/USD", "USDJPY=X": "USD/JPY",
    "USDCHF=X": "USD/CHF", "AUDUSD=X": "AUD/USD", "NZDUSD=X": "NZD/USD",
    "USDCAD=X": "USD/CAD", "EURGBP=X": "EUR/GBP", "EURJPY=X": "EUR/JPY",
    "GBPJPY=X": "GBP/JPY", "AUDJPY=X": "AUD/JPY", "CADJPY=X": "CAD/JPY",
    "EURCHF=X": "EUR/CHF", "EURAUD=X": "EUR/AUD", "EURNZD=X": "EUR/NZD",
    "GBPAUD=X": "GBP/AUD", "GBPCAD=X": "GBP/CAD", "GBPCHF=X": "GBP/CHF",
    "GBPNZD=X": "GBP/NZD", "AUDCAD=X": "AUD/CAD", "AUDCHF=X": "AUD/CHF",
    "CADCHF=X": "CAD/CHF", "NZDCAD=X": "NZD/CAD", "NZDCHF=X": "NZD/CHF",
}

INTERVALS = {"1d": "1day", "1h": "1h", "15m": "15min", "4h": "4h"}


def _empty_bars():
    # Avoid importing the scanner at module load time; run_forex_v7 imports us
    # after its underlying scanner has been initialized.
    return None


def fetch_twelve(symbol: str, interval: str, outputsize: int = 500):
    if not API_KEY or symbol not in FOREX_SYMBOLS or interval not in INTERVALS:
        return None

    try:
        r = requests.get(
            TD_URL,
            params={
                "symbol": FOREX_SYMBOLS[symbol],
                "interval": INTERVALS[interval],
                "outputsize": outputsize,
                "timezone": "UTC",
                "apikey": API_KEY,
            },
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        values = data.get("values")
        if not isinstance(values, list) or len(values) < 60:
            return None

        rows = []
        for item in reversed(values):
            dt = str(item.get("datetime", "")).strip()
            try:
                ts = int(datetime.fromisoformat(dt.replace("Z", "+00:00")).replace(tzinfo=timezone.utc).timestamp())
            except ValueError:
                # Twelve Data forex timestamps are normally UTC in this request.
                try:
                    ts = int(datetime.strptime(dt, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp())
                except ValueError:
                    continue
            try:
                o = float(item["open"]); h = float(item["high"]); l = float(item["low"]); c = float(item["close"])
                v = float(item.get("volume") or 0.0)
            except (TypeError, ValueError, KeyError):
                continue
            rows.append((ts, o, h, l, c, v))

        if len(rows) < 60:
            return None

        # Import the scanner's Bars class only after a successful request.
        import forex_intraday_scanner_v3 as scanner
        return scanner.Bars(
            [x[0] for x in rows], [x[1] for x in rows], [x[2] for x in rows],
            [x[3] for x in rows], [x[4] for x in rows], [x[5] for x in rows],
        )
    except Exception:
        return None


def install_fetch_override(scanner_module) -> str:
    """Patch the scanner so Forex candles prefer Twelve Data and fall back to Yahoo."""
    original_fetch: Callable = scanner_module.fetch

    def fetch(symbol: str, interval: str, range_: str):
        td = fetch_twelve(symbol, interval)
        if td is not None:
            return td
        return original_fetch(symbol, interval, range_)

    scanner_module.fetch = fetch
    return "Twelve Data + Yahoo fallback" if API_KEY else "Yahoo (Twelve Data key absent)"
