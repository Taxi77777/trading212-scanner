from __future__ import annotations

"""Accès aux données de marché et indicateurs de base.

Source : l'endpoint chart de Yahoo Finance, seul flux gratuit vérifié couvrant
les huit places visées (testé : US, Paris, Xetra, LSE, Milan — HTTP 200).
Les fondamentaux ne passent pas par ici : ``quoteSummary`` renvoie 401 depuis
2026, ils viennent de SEC EDGAR (voir ``fundamentals.py``).

Budget de requêtes
------------------
Le §4 demande six unités de temps. Les interroger toutes sur 550 valeurs ferait
plus de 3 000 requêtes par scan, ce que Yahoo n'accepte pas. Le §4 dit aussi que
le **Daily détermine la structure** et que l'intraday sert au *timing* — la
lecture se fait donc en deux passes :

* **passe 1**, tout l'univers : un seul appel Daily sur 5 ans, dont le Weekly
  est ré-échantillonné. Structure, base, résistance, OBV, compression, force
  relative en sortent entièrement.
* **passe 2**, seulement les meilleurs candidats : H1 puis M15 pour le timing,
  le 4H étant ré-échantillonné depuis le H1.

Environ 600 requêtes par scan complet au lieu de 3 000.
"""

import logging
import random
import threading
import time
from dataclasses import dataclass
from statistics import fmean

import requests

LOG = logging.getLogger("stockscan.data")

YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) StockScan/1.0"

# Plafonds Yahoo constatés : 1h ≤ 730 j, 15m/30m ≤ 60 j, 1d/1wk sans limite utile.
DAILY = ("1d", "5y")
HOURLY = ("1h", "60d")
M30 = ("30m", "1mo")
M15 = ("15m", "1mo")

WEEK = 7 * 86400
FOUR_HOURS = 4 * 3600


@dataclass
class Bars:
    ts: list[int]
    open: list[float]
    high: list[float]
    low: list[float]
    close: list[float]
    volume: list[float]
    # Yahoo renvoie le nom de la societe et la devise REELLE dans le meme appel.
    # Les recuperer ici ne coute aucune requete supplementaire et evite deux
    # erreurs : afficher « REC » a quelqu'un qui cherche Recordati, et afficher
    # « 1295 GBP » pour une valeur londonienne cotee en PENCE — un facteur 100.
    name: str = ""
    currency: str = ""

    def __len__(self) -> int:
        return len(self.close)

    def tail(self, n: int) -> "Bars":
        return Bars(self.ts[-n:], self.open[-n:], self.high[-n:],
                    self.low[-n:], self.close[-n:], self.volume[-n:],
                    self.name, self.currency)

    def head(self, n: int) -> "Bars":
        return Bars(self.ts[:n], self.open[:n], self.high[:n],
                    self.low[:n], self.close[:n], self.volume[:n],
                    self.name, self.currency)


class Throttle:
    """Limiteur de débit simple, partagé par tous les threads."""

    def __init__(self, per_second: float):
        self._interval = 1.0 / per_second if per_second > 0 else 0.0
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        if self._interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            if now < self._next:
                delay = self._next - now
            else:
                delay = 0.0
            self._next = max(now, self._next) + self._interval
        if delay > 0:
            time.sleep(delay)


class MarketData:
    def __init__(self, per_second: float = 6.0, retries: int = 3, timeout: float = 20.0):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
        self.throttle = Throttle(per_second)
        self.retries = retries
        self.timeout = timeout
        self.stats = {"calls": 0, "ok": 0, "empty": 0, "http_error": 0, "network_error": 0}
        self._lock = threading.Lock()

    def _bump(self, key: str) -> None:
        with self._lock:
            self.stats[key] = self.stats.get(key, 0) + 1

    def fetch(self, symbol: str, interval: str, range_: str) -> Bars | None:
        """OHLCV pour un symbole. ``None`` si la série est absente ou trop courte."""
        params = {"range": range_, "interval": interval,
                  "includePrePost": "false", "events": "div,splits"}
        for attempt in range(self.retries):
            self.throttle.wait()
            self._bump("calls")
            try:
                r = self.session.get(f"{YAHOO}/{symbol}", params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                self._bump("network_error")
                LOG.debug("%s %s: réseau %s", symbol, interval, exc)
                time.sleep(0.5 * (attempt + 1) + random.random() * 0.3)
                continue

            if r.status_code == 429 or 500 <= r.status_code < 600:
                self._bump("http_error")
                time.sleep(1.5 * (attempt + 1) + random.random())
                continue
            if r.status_code != 200:
                self._bump("http_error")
                LOG.debug("%s %s: HTTP %s", symbol, interval, r.status_code)
                return None

            try:
                result = r.json()["chart"]["result"][0]
                stamps = result.get("timestamp") or []
                q = result["indicators"]["quote"][0]
                meta = result.get("meta") or {}
            except (ValueError, KeyError, TypeError, IndexError):
                self._bump("empty")
                return None

            rows = []
            for i, t in enumerate(stamps):
                vals = (q["open"][i], q["high"][i], q["low"][i], q["close"][i], q["volume"][i])
                if all(v is not None for v in vals):
                    rows.append((int(t), *(float(v) for v in vals)))
            if len(rows) < 30:
                self._bump("empty")
                return None
            self._bump("ok")
            nom = (meta.get("longName") or meta.get("shortName") or "").strip()
            devise = (meta.get("currency") or "").strip()
            return Bars([x[0] for x in rows], [x[1] for x in rows], [x[2] for x in rows],
                        [x[3] for x in rows], [x[4] for x in rows], [x[5] for x in rows],
                        nom, devise)
        self._bump("empty")
        return None

    def daily(self, symbol: str) -> Bars | None:
        return self.fetch(symbol, *DAILY)

    def hourly(self, symbol: str) -> Bars | None:
        return self.fetch(symbol, *HOURLY)

    def m15(self, symbol: str) -> Bars | None:
        return self.fetch(symbol, *M15)


# --------------------------------------------------------------------------- #
# Ré-échantillonnage
# --------------------------------------------------------------------------- #
def resample(bars: Bars | None, seconds: int, min_bars_per_bucket: int = 1) -> Bars | None:
    """Agrège des bougies vers une unité de temps supérieure."""
    if not bars or len(bars) < 2:
        return None
    buckets: dict[int, list[int]] = {}
    for i, t in enumerate(bars.ts):
        buckets.setdefault(t - (t % seconds), []).append(i)
    rows = []
    for key in sorted(buckets):
        idx = buckets[key]
        if len(idx) < min_bars_per_bucket:
            continue
        rows.append((key, bars.open[idx[0]],
                     max(bars.high[i] for i in idx),
                     min(bars.low[i] for i in idx),
                     bars.close[idx[-1]],
                     sum(bars.volume[i] for i in idx)))
    if len(rows) < 10:
        return None
    return Bars([x[0] for x in rows], [x[1] for x in rows], [x[2] for x in rows],
                [x[3] for x in rows], [x[4] for x in rows], [x[5] for x in rows])


def weekly(daily_bars: Bars | None) -> Bars | None:
    """Weekly depuis le Daily — évite une requête par valeur."""
    return resample(daily_bars, WEEK)


def four_hourly(hourly_bars: Bars | None) -> Bars | None:
    return resample(hourly_bars, FOUR_HOURS, min_bars_per_bucket=2)


# --------------------------------------------------------------------------- #
# Indicateurs
# --------------------------------------------------------------------------- #
def sma(values: list[float], period: int) -> float | None:
    if len(values) < period or period <= 0:
        return None
    return fmean(values[-period:])


def sma_series(values: list[float], period: int) -> list[float]:
    if len(values) < period or period <= 0:
        return []
    out, running = [], sum(values[:period])
    out.append(running / period)
    for i in range(period, len(values)):
        running += values[i] - values[i - period]
        out.append(running / period)
    return out


def ema_series(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    k = 2 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def ema(values: list[float], period: int) -> float | None:
    series = ema_series(values, period)
    return series[-1] if series else None


def true_ranges(bars: Bars) -> list[float]:
    out, prev = [], bars.close[0]
    for h, l, c in zip(bars.high[1:], bars.low[1:], bars.close[1:]):
        out.append(max(h - l, abs(h - prev), abs(l - prev)))
        prev = c
    return out


def atr(bars: Bars | None, period: int = 14) -> float | None:
    if not bars or len(bars) < period + 1:
        return None
    return fmean(true_ranges(bars)[-period:])


def atr_series(bars: Bars, period: int = 14) -> list[float]:
    """ATR glissant, indexé sur les bougies à partir de la période+1."""
    tr = true_ranges(bars)
    if len(tr) < period:
        return []
    prefix = [0.0]
    for v in tr:
        prefix.append(prefix[-1] + v)
    return [(prefix[i] - prefix[i - period]) / period for i in range(period, len(tr) + 1)]


def atr_pct_series(bars: Bars, period: int = 14) -> list[float]:
    """ATR exprimé en % du prix, aligné sur les bougies.

    Comparer des ATR bruts à travers le temps est faux : une valeur passée de
    40 € à 85 € aura mécaniquement un ATR deux fois plus gros à volatilité
    relative identique. Toute comparaison de régime de volatilité doit se faire
    en pourcentage.
    """
    raw = atr_series(bars, period)
    out = []
    for k, value in enumerate(raw):
        idx = k + period
        if idx >= len(bars.close):
            break
        price = bars.close[idx]
        if price > 0:
            out.append(value / price * 100)
    return out


def obv_series(bars: Bars | None) -> list[float]:
    """On-Balance Volume (§8)."""
    if not bars or len(bars) < 2:
        return []
    out = [0.0]
    for i in range(1, len(bars)):
        if bars.close[i] > bars.close[i - 1]:
            out.append(out[-1] + bars.volume[i])
        elif bars.close[i] < bars.close[i - 1]:
            out.append(out[-1] - bars.volume[i])
        else:
            out.append(out[-1])
    return out


def pct_change(values: list[float], lookback: int) -> float | None:
    if len(values) <= lookback or lookback <= 0:
        return None
    past = values[-lookback - 1]
    if past == 0:
        return None
    return (values[-1] / past - 1) * 100


def slope_pct(values: list[float], lookback: int) -> float | None:
    """Pente d'une série, en % de sa valeur courante."""
    if len(values) <= lookback or lookback <= 0 or values[-1] == 0:
        return None
    return (values[-1] - values[-lookback - 1]) / abs(values[-1]) * 100


def swing_highs(bars: Bars, left: int = 3, right: int = 3) -> list[tuple[int, float]]:
    """Sommets locaux confirmés — (index, prix)."""
    out = []
    for i in range(left, len(bars) - right):
        window = bars.high[i - left:i + right + 1]
        if bars.high[i] == max(window) and window.count(bars.high[i]) == 1:
            out.append((i, bars.high[i]))
    return out


def swing_lows(bars: Bars, left: int = 3, right: int = 3) -> list[tuple[int, float]]:
    out = []
    for i in range(left, len(bars) - right):
        window = bars.low[i - left:i + right + 1]
        if bars.low[i] == min(window) and window.count(bars.low[i]) == 1:
            out.append((i, bars.low[i]))
    return out
