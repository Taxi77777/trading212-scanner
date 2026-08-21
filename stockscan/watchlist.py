"""Surveillance des plans en cours : prévenir au moment où ça se passe.

Le scan tourne une fois par jour sur des bougies journalières : le relancer
toutes les demi-heures redonnerait mot pour mot le même résultat, puisque la
bougie du jour ne se referme qu'à la clôture. Ce qui change en séance, ce n'est
pas l'analyse — c'est le COURS.

Ce module garde donc les quelques plans retenus et va voir, en intraséance, si
l'un d'eux franchit son prix d'entrée, casse sa sortie de secours ou atteint son
objectif. Le reste du temps, il se tait. Une alerte qui se répète toutes les
trente minutes cesse d'être lue au bout d'une journée.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field

from . import market_data as md

DECLENCHE, STOPPE, OBJECTIF = "DECLENCHE", "STOPPE", "OBJECTIF"

EVENT_LABEL = {
    DECLENCHE: "🔔 Entrée franchie",
    STOPPE: "🛑 Sortie de secours touchée",
    OBJECTIF: "🎯 Objectif atteint",
}


@dataclass
class Watch:
    ticker: str = ""
    symbol: str = ""
    name: str = ""
    market_label: str = ""
    currency: str = ""
    phase: str = ""
    entry: float = 0.0
    stop: float = 0.0
    target: float = 0.0
    created_ts: int = 0          # date d'émission du plan
    triggered: bool = False      # l'entrée a été franchie
    closed: bool = False         # stop ou objectif atteint : le suivi s'arrête
    outcome: str = ""

    @property
    def alive(self) -> bool:
        return not self.closed


@dataclass
class Event:
    watch: Watch
    kind: str
    price: float
    notes: list[str] = field(default_factory=list)


def since(bars: md.Bars | None, ts: int) -> md.Bars | None:
    """Ne garde que les barres postérieures à l'émission du plan.

    Sans ce filtre, un plan dont l'entrée est sous le plus haut des jours
    précédents serait déclaré « déclenché » dès la première vérification, sur un
    mouvement qui a eu lieu avant même que le signal existe.
    """
    if not bars or len(bars) == 0:
        return None
    gardees = [i for i, t in enumerate(bars.ts) if t >= ts]
    if not gardees:
        return None
    start = gardees[0]
    return md.Bars(bars.ts[start:], bars.open[start:], bars.high[start:],
                   bars.low[start:], bars.close[start:], bars.volume[start:],
                   bars.name, bars.currency)


def inspect(item: Watch, bars: md.Bars | None) -> Event | None:
    """Un seul événement par passage, le plus grave d'abord.

    Si une même séance touche l'entrée puis le stop, c'est le stop qui compte :
    annoncer « objectif atteint » alors que la position est morte serait le
    genre de mensonge poli qu'un outil de trading ne peut pas se permettre.
    """
    if not item.alive:
        return None
    fenetre = since(bars, item.created_ts)
    if not fenetre or len(fenetre) == 0:
        return None

    plus_haut = max(fenetre.high)
    plus_bas = min(fenetre.low)
    dernier = fenetre.close[-1]

    if not item.triggered:
        if item.entry > 0 and plus_haut >= item.entry:
            item.triggered = True
            return Event(item, DECLENCHE, dernier,
                         [f"Le cours a franchi {item.entry:.2f}"])
        return None

    if item.stop > 0 and plus_bas <= item.stop:
        item.closed = True
        item.outcome = STOPPE
        return Event(item, STOPPE, dernier,
                     [f"Le cours est passé sous {item.stop:.2f}"])
    if item.target > 0 and plus_haut >= item.target:
        item.closed = True
        item.outcome = OBJECTIF
        return Event(item, OBJECTIF, dernier,
                     [f"Le cours a atteint {item.target:.2f}"])
    return None


def load(path: str) -> list[Watch]:
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            rows = json.load(fh)
    except (ValueError, OSError):
        return []
    out = []
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict):
            connus = {k: v for k, v in row.items() if k in Watch.__annotations__}
            out.append(Watch(**connus))
    return out


def save(path: str, items: list[Watch]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump([asdict(w) for w in items], fh, ensure_ascii=False, indent=2)


def merge(anciens: list[Watch], nouveaux: list[Watch]) -> list[Watch]:
    """Un nouveau scan ne doit pas effacer un suivi en cours.

    Une valeur encore vivante garde son état — sinon un plan déjà déclenché
    serait re-signalé le lendemain comme s'il venait d'être émis.
    """
    par_ticker = {w.ticker: w for w in anciens if w.alive}
    for neuf in nouveaux:
        if neuf.ticker not in par_ticker:
            par_ticker[neuf.ticker] = neuf
    return list(par_ticker.values())


def from_candidate(c, ts: int) -> Watch | None:
    if not getattr(c, "plan", None) or not c.plan.tradeable or not c.plan.targets:
        return None
    return Watch(ticker=c.ticker, symbol=c.symbol,
                 name=getattr(c, "name", "") or c.ticker,
                 market_label=c.market_label, currency=c.currency,
                 phase=c.phase.name, entry=c.plan.entry, stop=c.plan.stop,
                 target=c.plan.targets[0].price, created_ts=ts)
