"""Fondamentaux via SEC EDGAR (XBRL companyfacts) — valeurs américaines.

Yahoo renvoie 401 sur quoteSummary : aucun fondamental n'est disponible par ce
canal. EDGAR est gratuit, sans clé, et fait autorité — mais il ne couvre que les
sociétés cotées aux États-Unis. Pour l'Europe, ce module renvoie « indisponible »
et le score fondamental n'est tout simplement pas appliqué. Une absence de
donnée ne doit jamais se transformer en note moyenne inventée.

Piège XBRL : un 10-K publie l'exercice complet, pas le quatrième trimestre. On
ne peut donc pas additionner naïvement toutes les durées trouvées — elles se
chevauchent. On sépare les durées trimestrielles des durées annuelles et on ne
mélange jamais les deux dans une même somme.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

# `os.environ.get` ne rend le defaut que si la variable est ABSENTE. Dans une
# GitHub Action, un secret non defini donne une variable PRESENTE et VIDE :
# l'en-tete partait vide et SEC EDGAR refuse les requetes sans User-Agent.
DEFAULT_UA = (os.environ.get("SEC_USER_AGENT", "").strip()
              or "stockscan/1.0 (open-source equity scanner; contact via GitHub repository)")

QUARTER = (80, 100)
YEAR = (350, 380)

REVENUE_TAGS = ("RevenueFromContractWithCustomerExcludingAssessedTax",
                "RevenueFromContractWithCustomerIncludingAssessedTax",
                "Revenues", "SalesRevenueNet")
NET_INCOME_TAGS = ("NetIncomeLoss",)
GROSS_TAGS = ("GrossProfit",)
OPERATING_TAGS = ("OperatingIncomeLoss",)
EPS_TAGS = ("EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted")
EQUITY_TAGS = ("StockholdersEquity",
               "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest")
LIABILITY_TAGS = ("Liabilities",)


def _days(start: str, end: str) -> int:
    fmt = "%Y-%m-%d"
    return int((time.mktime(time.strptime(end, fmt))
                - time.mktime(time.strptime(start, fmt))) / 86400 + 0.5)


def durations(facts: dict, tags, unit: str = "USD", span=QUARTER) -> list[tuple[str, float]]:
    """Séries de flux (chiffre d'affaires, résultat) sur une durée donnée.

    Une même période peut être publiée plusieurs fois (10-Q puis 10-K corrigé) :
    on garde le dépôt le plus récent, pas le premier rencontré.
    """
    best: dict[tuple[str, str], tuple[str, float]] = {}
    for tag in tags:
        entries = (facts.get("facts", {}).get("us-gaap", {})
                   .get(tag, {}).get("units", {}).get(unit, []))
        for e in entries:
            start, end, val = e.get("start"), e.get("end"), e.get("val")
            if not start or not end or val is None:
                continue
            try:
                length = _days(start, end)
            except ValueError:
                continue
            if not (span[0] <= length <= span[1]):
                continue
            key = (start, end)
            filed = e.get("filed", "")
            if key not in best or filed > best[key][0]:
                best[key] = (filed, float(val))
        if best:
            break            # premier tag qui donne quelque chose : on s'y tient
    return [(end, v) for (_s, end), (_f, v) in sorted(best.items(), key=lambda kv: kv[0][1])]


def instants(facts: dict, tags, unit: str = "USD") -> list[tuple[str, float]]:
    """Séries de stock (capitaux propres, dettes) : une date, pas une durée."""
    best: dict[str, tuple[str, float]] = {}
    for tag in tags:
        entries = (facts.get("facts", {}).get("us-gaap", {})
                   .get(tag, {}).get("units", {}).get(unit, []))
        for e in entries:
            end, val = e.get("end"), e.get("val")
            if not end or val is None or e.get("start"):
                continue
            filed = e.get("filed", "")
            if end not in best or filed > best[end][0]:
                best[end] = (filed, float(val))
        if best:
            break
    return [(end, v) for end, (_f, v) in sorted(best.items())]


def ttm(series: list[tuple[str, float]], offset: int = 0) -> float | None:
    """Somme des quatre trimestres se terminant `offset` trimestres plus tôt."""
    end = len(series) - offset
    if end < 4:
        return None
    window = series[end - 4:end]
    return sum(v for _d, v in window)


def growth_pct(series: list[tuple[str, float]]) -> float | None:
    now, before = ttm(series), ttm(series, offset=4)
    if now is None or before is None or before == 0:
        return None
    if before < 0:                       # d'une perte à un profit : le % n'a pas de sens
        return None
    return (now / before - 1.0) * 100.0


@dataclass
class Fundamentals:
    available: bool = False
    cik: str = ""
    revenue_ttm: float | None = None
    revenue_growth_pct: float | None = None
    eps_ttm: float | None = None
    eps_growth_pct: float | None = None
    gross_margin_pct: float | None = None
    operating_margin_pct: float | None = None
    debt_to_equity: float | None = None
    roe_pct: float | None = None
    score: float | None = None           # 0 à 10, None si rien de fiable
    notes: list[str] = field(default_factory=list)


def analyse(facts: dict | None, *, cik: str = "") -> Fundamentals:
    out = Fundamentals(cik=cik)
    if not facts:
        out.notes.append("Fondamentaux indisponibles — société hors périmètre EDGAR")
        return out

    rev = durations(facts, REVENUE_TAGS)
    net = durations(facts, NET_INCOME_TAGS)
    gross = durations(facts, GROSS_TAGS)
    oper = durations(facts, OPERATING_TAGS)
    eps = durations(facts, EPS_TAGS, unit="USD/shares")
    equity = instants(facts, EQUITY_TAGS)
    liab = instants(facts, LIABILITY_TAGS)

    out.revenue_ttm = ttm(rev)
    out.revenue_growth_pct = growth_pct(rev)
    out.eps_ttm = ttm(eps)
    out.eps_growth_pct = growth_pct(eps)

    gross_ttm, oper_ttm, net_ttm = ttm(gross), ttm(oper), ttm(net)
    if out.revenue_ttm:
        if gross_ttm is not None:
            out.gross_margin_pct = gross_ttm / out.revenue_ttm * 100.0
        if oper_ttm is not None:
            out.operating_margin_pct = oper_ttm / out.revenue_ttm * 100.0
    if equity and equity[-1][1] > 0:
        eq = equity[-1][1]
        if liab:
            out.debt_to_equity = liab[-1][1] / eq
        if net_ttm is not None:
            out.roe_pct = net_ttm / eq * 100.0

    out.available = any(v is not None for v in
                        (out.revenue_growth_pct, out.eps_growth_pct,
                         out.operating_margin_pct, out.roe_pct))
    if not out.available:
        out.notes.append("Aucune série exploitable dans les dépôts EDGAR")
        return out

    out.score = score_fundamentals(out)
    return out


def score_fundamentals(f: Fundamentals) -> float:
    """0 à 10, autour d'un neutre à 5.

    Le neutre compte : une société sans donnée ne doit pas être avantagée ni
    punie par rapport à une société correcte. Le score fondamental n'est qu'un
    ajustement — la configuration technique reste le sujet.
    """
    score = 5.0
    if f.revenue_growth_pct is not None:
        g = f.revenue_growth_pct
        score += 2.0 if g >= 20 else 1.2 if g >= 10 else 0.4 if g >= 3 else \
            -1.0 if g >= -5 else -2.0
        f.notes.append(f"Chiffre d'affaires {g:+.1f} % sur un an")
    if f.eps_growth_pct is not None:
        g = f.eps_growth_pct
        score += 2.0 if g >= 25 else 1.2 if g >= 10 else 0.4 if g >= 0 else -1.5
        f.notes.append(f"Bénéfice par action {g:+.1f} % sur un an")
    if f.operating_margin_pct is not None:
        m = f.operating_margin_pct
        score += 1.5 if m >= 20 else 0.8 if m >= 10 else 0.0 if m >= 3 else -1.5
        f.notes.append(f"Marge opérationnelle {m:.1f} %")
    if f.roe_pct is not None:
        r = f.roe_pct
        score += 1.0 if r >= 18 else 0.5 if r >= 10 else -1.0 if r < 0 else 0.0
    if f.debt_to_equity is not None and f.debt_to_equity > 3.0:
        score -= 1.0
        f.notes.append(f"Endettement élevé (dettes/capitaux propres {f.debt_to_equity:.1f})")
    return max(0.0, min(10.0, score))


# --------------------------------------------------------------------------
# Accès réseau — isolé pour que le reste du module soit testable hors ligne
# --------------------------------------------------------------------------
class SecClient:
    def __init__(self, user_agent: str = DEFAULT_UA, per_second: float = 6.0,
                 timeout: float = 20.0):
        self.user_agent = user_agent
        self.timeout = timeout
        self._min_gap = 1.0 / per_second if per_second > 0 else 0.0
        self._last = 0.0
        self._tickers: dict[str, str] | None = None
        self.stats = {"calls": 0, "ok": 0, "error": 0}

    def _wait(self) -> None:
        gap = time.monotonic() - self._last
        if gap < self._min_gap:
            time.sleep(self._min_gap - gap)
        self._last = time.monotonic()

    def _get(self, url: str) -> dict | None:
        self._wait()
        self.stats["calls"] += 1
        req = urllib.request.Request(url, headers={
            "User-Agent": self.user_agent,
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    import gzip
                    raw = gzip.decompress(raw)
                self.stats["ok"] += 1
                return json.loads(raw.decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            self.stats["error"] += 1
            return None

    def tickers(self) -> dict[str, str]:
        """TICKER -> CIK sur 10 chiffres. Mis en cache pour la durée du scan."""
        if self._tickers is not None:
            return self._tickers
        data = self._get(TICKERS_URL)
        table: dict[str, str] = {}
        if isinstance(data, dict):
            for row in data.values():
                if isinstance(row, dict) and row.get("ticker") and row.get("cik_str"):
                    table[str(row["ticker"]).upper()] = str(row["cik_str"]).zfill(10)
        self._tickers = table
        return table

    def facts(self, ticker: str) -> tuple[dict | None, str]:
        cik = self.tickers().get(ticker.upper())
        if not cik:
            return None, ""
        return self._get(FACTS_URL.format(cik=cik)), cik

    def fundamentals(self, ticker: str) -> Fundamentals:
        data, cik = self.facts(ticker)
        return analyse(data, cik=cik)
