import scanner_v2
from company_names import company_name

_original_fmt = scanner_v2.fmt

def fmt_with_full_name(candidate, overlay):
    text = _original_fmt(candidate, overlay)
    symbol = candidate.signal.symbol
    text = text.replace(f"{candidate.signal.side} {symbol}\n", f"{candidate.signal.side} {symbol} — {company_name(symbol)}\n", 1)
    return text

scanner_v2.fmt = fmt_with_full_name

if __name__ == "__main__":
    raise SystemExit(scanner_v2.main())
