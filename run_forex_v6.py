from __future__ import annotations

import run_forex_v5 as v5

scanner = v5.scanner

# Keep real entries strict, but allow the engine to report strong-enough
# intraday setups instead of returning zero simply because the final entry
# confirmation is not ready yet.
scanner.SETUP_MIN = 30
scanner.FINAL_MIN = 68

if __name__ == "__main__":
    raise SystemExit(scanner.main())
