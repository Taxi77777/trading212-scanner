from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import run_forex_v6 as v6

scanner = v6.scanner

# Production thresholds: setup alerts are allowed at 30, real entries remain 68.
scanner.SETUP_MIN = 30
scanner.FINAL_MIN = 68


def main() -> int:
    # First run the normal production engine exactly as configured.
    rc = scanner.main()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
