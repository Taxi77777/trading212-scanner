from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import run_forex_v6 as v6
import telegram_signals as tg

s = v6.scanner
pairs = list(s.PAIRS)

data_ok = h4_ok = 0
news_blocked = 0
usable = 0
failed = []

with ThreadPoolExecutor(max_workers=12) as ex:
    futs = {sym: ex.submit(s.fetch, sym, '1h', '6mo') for sym in pairs}
    for sym, fut in futs.items():
        try:
            h1 = fut.result()
        except Exception:
            h1 = None
        if h1 is None:
            failed.append(sym)
            continue
        data_ok += 1
        h4 = s.resample_h4(h1)
        if h4 is not None:
            h4_ok += 1
        if h4 is not None:
            usable += 1

events = s.calendar_events()
for sym in pairs:
    try:
        _, blocked = s.event_risk(s.PAIRS[sym][2], events)
        news_blocked += int(blocked)
    except Exception:
        pass

msg = (f"🧪 FOREX PREFLIGHT\n"
       f"Paires : {len(pairs)}\n"
       f"H1 données : {data_ok}/{len(pairs)}\n"
       f"H4 construits : {h4_ok}/{len(pairs)}\n"
       f"News high impact bloquées : {news_blocked}\n"
       f"Calendrier chargé : {len(events)} événements\n"
       f"Session UTC : {s.session_name()}\n"
       f"Seuil SETUP : {s.SETUP_MIN} | ENTRY : {s.FINAL_MIN}\n"
       f"Échecs data : {', '.join(failed[:8]) if failed else 'aucun'}")

tg.telegram_send(msg)
