"""Compatibility wrapper for the Daily + 1H scanner.

Fixes legacy ticker/macros before invoking the core engine:
- Block migrated from SQ to XYZ.
- Macro regime currently consumes exactly SPY, QQQ, ^VIX, UUP and TLT.
"""
from __future__ import annotations

import daily_1h_longterm_scanner as scanner

# Keep the engine's macro_regime unpacking consistent with the intended inputs.
scanner.MACRO = ["SPY", "QQQ", "^VIX", "UUP", "TLT"]

# Replace the legacy Block ticker without changing the core engine.
scanner.base.SYMBOLS = ["XYZ" if s == "SQ" else s for s in scanner.base.SYMBOLS]
scanner.NAMES["XYZ"] = "Block, Inc."
scanner.NAMES.pop("SQ", None)

if __name__ == "__main__":
    raise SystemExit(scanner.main())
