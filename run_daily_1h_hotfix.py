"""Compatibility wrapper for the Daily + 1H scanner."""
from __future__ import annotations

import daily_1h_longterm_scanner as scanner
from expanded_universe import EXPANDED_SYMBOLS

# Macro inputs must match the five-value unpacking in macro_regime().
scanner.MACRO = ["SPY", "QQQ", "^VIX", "UUP", "TLT"]

# Use the expanded investable universe and repair the legacy Block ticker.
scanner.base.SYMBOLS = list(dict.fromkeys(["XYZ" if s == "SQ" else s for s in EXPANDED_SYMBOLS]))
scanner.NAMES["XYZ"] = "Block, Inc."
scanner.NAMES.pop("SQ", None)

if __name__ == "__main__":
    raise SystemExit(scanner.main())
