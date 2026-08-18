from __future__ import annotations

import re
import forex_intraday_scanner_v3 as scanner
import telegram_signals as tg
from central_bank_rates import load_rates, assessment

rates, rate_source = load_rates()
stats = {"checked": 0, "blocked": 0}
_original_send = tg.telegram_send


def _send(message: str):
    if "SIGNAL FOREX INTRADAY" in message:
        stats["checked"] += 1
        m_pair = re.search(r"SIGNAL FOREX INTRADAY\s+[—-]\s+([A-Z]{3}/[A-Z]{3})", message)
        m_side = re.search(r"Direction\s*:\s*(ACHAT|VENTE)", message)
        if m_pair and m_side:
            pair = m_pair.group(1)
            side = "BUY" if m_side.group(1) == "ACHAT" else "SELL"
            verdict, diff = assessment(pair, side, rates)
            diff_txt = "inconnu" if diff is None else f"{diff:+.2f} pp"
            rate_line = f"Taux banques centrales : {verdict} ({diff_txt})"
            source_line = f"Source taux : {rate_source}"
            message = message.replace("DXY :", rate_line + "\n" + source_line + "\nDXY :", 1)
            # Only hard-block a very strong policy-rate contradiction.
            if verdict == "TAUX_FORTEMENT_CONTRE":
                stats["blocked"] += 1
                return False
    if "💱 Scan FOREX D1+H4+H1+M15:" in message:
        message += f" | Diff taux vérifié {stats['checked']} | bloqués taux {stats['blocked']} | source {rate_source}"
    return _original_send(message)


tg.telegram_send = _send
scanner.base.telegram_send = _send

if __name__ == "__main__":
    raise SystemExit(scanner.main())
