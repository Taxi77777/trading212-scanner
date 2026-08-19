"""Offline test package for the Forex scanner.

Importing this package makes the whole suite hermetic. The v4 rate overlay
otherwise fetches live central-bank rates over HTTP the first time
``build_signal`` runs, which makes every downstream assertion depend on today's
policy rates and on the runner having network access — the exact reason the
suite passed locally and failed in CI.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import run_forex_v4  # noqa: E402  (import order is deliberate)

# Pre-seed the lazy cache: no HTTP request, no dependency on live rates.
run_forex_v4._rates_cache["rates"] = {}
run_forex_v4._rates_cache["source"] = "tests (hors ligne)"
