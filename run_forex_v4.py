from __future__ import annotations

import forex_intraday_scanner_v3 as scanner
import central_bank_rates

scanner.PAIRS = {
    "EURUSD=X": ("EUR", "USD", "EUR/USD"), "GBPUSD=X": ("GBP", "USD", "GBP/USD"),
    "USDJPY=X": ("USD", "JPY", "USD/JPY"), "USDCHF=X": ("USD", "CHF", "USD/CHF"),
    "AUDUSD=X": ("AUD", "USD", "AUD/USD"), "NZDUSD=X": ("NZD", "USD", "NZD/USD"),
    "USDCAD=X": ("USD", "CAD", "USD/CAD"), "EURGBP=X": ("EUR", "GBP", "EUR/GBP"),
    "EURJPY=X": ("EUR", "JPY", "EUR/JPY"), "GBPJPY=X": ("GBP", "JPY", "GBP/JPY"),
    "AUDJPY=X": ("AUD", "JPY", "AUD/JPY"), "CADJPY=X": ("CAD", "JPY", "CAD/JPY"),
    "EURCHF=X": ("EUR", "CHF", "EUR/CHF"), "EURAUD=X": ("EUR", "AUD", "EUR/AUD"),
    "EURNZD=X": ("EUR", "NZD", "EUR/NZD"), "GBPAUD=X": ("GBP", "AUD", "GBP/AUD"),
    "GBPCAD=X": ("GBP", "CAD", "GBP/CAD"), "GBPCHF=X": ("GBP", "CHF", "GBP/CHF"),
    "GBPNZD=X": ("GBP", "NZD", "GBP/NZD"), "AUDCAD=X": ("AUD", "CAD", "AUD/CAD"),
    "AUDCHF=X": ("AUD", "CHF", "AUD/CHF"), "CADCHF=X": ("CAD", "CHF", "CAD/CHF"),
    "NZDCAD=X": ("NZD", "CAD", "NZD/CAD"), "NZDCHF=X": ("NZD", "CHF", "NZD/CHF"),
}

scanner.SETUP_MIN = 54
scanner.FINAL_MIN = 68

# Central-bank rates are fetched lazily: importing this module used to fire two
# blocking HTTP requests, which slowed every import (preflight, scanner, tests)
# and made offline test runs depend on the network.
_rates_cache: dict[str, object] = {}


def rates() -> dict:
    if "rates" not in _rates_cache:
        try:
            values, source = central_bank_rates.load_rates()
        except Exception as exc:  # never let the overlay break the engine
            values, source = {}, f"indisponible ({exc})"
        _rates_cache["rates"] = values
        _rates_cache["source"] = source
    return _rates_cache["rates"]  # type: ignore[return-value]


def rate_source() -> str:
    rates()
    return str(_rates_cache.get("source", "inconnu"))


_original_build = scanner.build_signal


def build_signal_with_rates(pair, frames, strength, macro, macro_reason, news, news_block):
    sig = _original_build(pair, frames, strength, macro, macro_reason, news, news_block)
    if sig is None:
        return None

    assessment, diff = central_bank_rates.assessment(sig.pair, sig.side, rates())

    if assessment == "TAUX_FORTEMENT_FAVORABLE":
        sig.score = min(100, sig.score + 4)
        sig.reasons.append("différentiel de taux fortement favorable")
    elif assessment == "TAUX_FAVORABLE":
        sig.score = min(100, sig.score + 2)
        sig.reasons.append("différentiel de taux favorable")
    elif assessment == "TAUX_CONTRE":
        sig.reasons.append("différentiel de taux contraire")
    elif assessment == "TAUX_FORTEMENT_CONTRE":
        return None

    # The rate overlay is applied before final signal classification.
    if sig.m15 == "CONFIRME" and sig.score >= scanner.FINAL_MIN:
        sig.state = "ENTREE"

    sig.reasons.append(
        f"Taux: {assessment} ({diff:+.2f} pp)" if diff is not None else "Taux: inconnu"
    )
    return sig


scanner.build_signal = build_signal_with_rates


if __name__ == "__main__":
    raise SystemExit(scanner.main())
