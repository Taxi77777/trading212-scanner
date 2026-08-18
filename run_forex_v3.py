from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import forex_intraday_scanner_v3 as scanner

CALENDAR_URL = os.getenv("FOREX_CALENDAR_URL", "https://nfs.faireconomy.media/ff_calendar_thisweek.json")
CALENDAR_FILE = Path("macro_calendar_runtime.json")


def normalize_calendar() -> None:
    # The public feed commonly provides ISO date strings plus impact/currency.
    # Normalize them to numeric timestamps consumed by the scanner.
    import requests
    try:
        r = requests.get(CALENDAR_URL, timeout=10, headers={"User-Agent": "T212Forex/3.1"})
        r.raise_for_status()
        raw = r.json()
        events = raw if isinstance(raw, list) else raw.get("events", [])
        out = []
        for ev in events:
            item = dict(ev)
            raw_ts = item.get("timestamp", item.get("time", item.get("date")))
            ts = None
            if raw_ts is not None:
                try:
                    ts = float(raw_ts)
                    if ts > 1e12:
                        ts /= 1000
                except (TypeError, ValueError):
                    try:
                        ts = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00")).timestamp()
                    except ValueError:
                        ts = None
            if ts is not None:
                item["timestamp"] = ts
                out.append(item)
        CALENDAR_FILE.write_text(json.dumps(out), encoding="utf-8")
    except Exception as exc:
        CALENDAR_FILE.write_text("[]", encoding="utf-8")
        print(f"calendar normalization failed: {exc}")


def calendar_events():
    try:
        return json.loads(CALENDAR_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


normalize_calendar()
scanner.calendar_events = calendar_events

if __name__ == "__main__":
    raise SystemExit(scanner.main())
