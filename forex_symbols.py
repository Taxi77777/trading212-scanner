from __future__ import annotations

"""Centralised Forex symbol normalisation.

Single source of truth for converting any human or technical spelling of a
currency pair into the canonical internal key used by the scanners.

Accepted inputs (all resolve to the same canonical key)::

    "EUR/USD"    -> "EURUSD=X"
    "EURUSD"     -> "EURUSD=X"
    "EURUSD=X"   -> "EURUSD=X"
    "eur-usd"    -> "EURUSD=X"
    "EUR_USD"    -> "EURUSD=X"
    "EUR USD"    -> "EURUSD=X"

The canonical key is the Yahoo Finance symbol, because that is what the data
layer actually queries. Human labels ("EUR/USD") are display-only and must
never be used to index a mapping directly.
"""

from typing import Iterable, Mapping

__all__ = [
    "CURRENCIES",
    "canonical",
    "label",
    "split",
    "is_known",
    "resolve_key",
    "register",
    "register_mapping",
    "known_symbols",
]

# ISO-4217 codes the scanners may legitimately encounter. Keeping this explicit
# avoids mis-parsing arbitrary 6-letter strings as a currency pair.
CURRENCIES: set[str] = {
    "USD", "EUR", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD",
    "SEK", "NOK", "DKK", "PLN", "CZK", "HUF", "TRY", "ZAR",
    "MXN", "SGD", "HKD", "CNH", "CNY", "INR", "KRW", "THB",
    "ILS", "RUB", "BRL", "XAU", "XAG",
}

_SEPARATORS = ("/", "-", "_", ".", " ", "\t", ":")

# Explicit aliases registered at runtime (label -> canonical symbol).
_ALIASES: dict[str, str] = {}


def _strip(value: object) -> str:
    text = str(value or "").strip().upper()
    for sep in _SEPARATORS:
        text = text.replace(sep, "")
    if text.endswith("=X"):
        text = text[:-2]
    return text


def canonical(value: object) -> str | None:
    """Return the canonical Yahoo symbol for *value*, or ``None``.

    Never raises. Accepts labels, bare pairs and Yahoo symbols alike.
    """
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None

    alias = _ALIASES.get(raw.upper())
    if alias:
        return alias

    compact = _strip(raw)
    alias = _ALIASES.get(compact)
    if alias:
        return alias

    if len(compact) != 6:
        return None
    base, quote = compact[:3], compact[3:]
    if base not in CURRENCIES or quote not in CURRENCIES or base == quote:
        return None
    return f"{base}{quote}=X"


def split(value: object) -> tuple[str, str] | None:
    """Return ``(base, quote)`` for *value*, or ``None`` when unresolvable."""
    symbol = canonical(value)
    if not symbol:
        return None
    core = symbol[:-2]
    return core[:3], core[3:]


def label(value: object) -> str | None:
    """Return the human display label (``"EUR/USD"``) for *value*."""
    parts = split(value)
    if not parts:
        return None
    return f"{parts[0]}/{parts[1]}"


def is_known(value: object) -> bool:
    return canonical(value) is not None


def resolve_key(value: object, mapping: Mapping[str, object]) -> str | None:
    """Resolve *value* to a key that actually exists in *mapping*.

    Handles mappings keyed by Yahoo symbol (the normal case) and, defensively,
    mappings keyed by label or by bare pair.
    """
    if not mapping:
        return None
    if value in mapping:
        return value  # type: ignore[return-value]

    symbol = canonical(value)
    if symbol is None:
        return None
    if symbol in mapping:
        return symbol

    for key in mapping:
        if canonical(key) == symbol:
            return key
    return None


def register(symbol: str, *aliases: str) -> str | None:
    """Register extra spellings pointing at *symbol*.

    Returns the canonical symbol, or ``None`` when *symbol* itself is invalid.
    """
    target = canonical(symbol)
    if target is None:
        return None
    for alias in (symbol, target, *aliases):
        text = str(alias or "").strip().upper()
        if text:
            _ALIASES[text] = target
            _ALIASES[_strip(text)] = target
    parts = split(target)
    if parts:
        _ALIASES[f"{parts[0]}/{parts[1]}"] = target
    return target


def register_mapping(pairs: Mapping[str, Iterable[object]]) -> int:
    """Register every entry of a scanner ``PAIRS`` mapping.

    ``PAIRS`` maps ``"EURUSD=X" -> ("EUR", "USD", "EUR/USD")``. Both the key and
    the display label become resolvable.
    """
    count = 0
    for symbol, values in pairs.items():
        extra: list[str] = []
        try:
            seq = list(values)
        except TypeError:
            seq = []
        if len(seq) >= 3 and seq[2]:
            extra.append(str(seq[2]))
        if len(seq) >= 2 and seq[0] and seq[1]:
            extra.append(f"{seq[0]}/{seq[1]}")
        if register(symbol, *extra) is not None:
            count += 1
    return count


def known_symbols() -> list[str]:
    return sorted(set(_ALIASES.values()))
