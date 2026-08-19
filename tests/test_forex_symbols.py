import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import forex_symbols as fx

REQUIRED = ["EUR/USD", "USD/CHF", "AUD/CHF", "CAD/CHF", "GBP/USD", "USD/JPY"]


class TestNormalisation(unittest.TestCase):
    def test_required_pairs_all_spellings(self):
        for label in REQUIRED:
            base, quote = label.split("/")
            expected = f"{base}{quote}=X"
            for spelling in (label, label.lower(), f"{base}{quote}", expected,
                             expected.lower(), f"{base}-{quote}", f"{base}_{quote}",
                             f" {base} {quote} "):
                with self.subTest(pair=label, spelling=spelling):
                    self.assertEqual(fx.canonical(spelling), expected)

    def test_label_and_split_roundtrip(self):
        for label in REQUIRED:
            self.assertEqual(fx.label(label), label)
            self.assertEqual(fx.split(label), tuple(label.split("/")))
            self.assertEqual(fx.label(fx.canonical(label)), label)

    def test_full_scanner_universe(self):
        import run_forex_v4  # extended 24-pair universe

        pairs = run_forex_v4.scanner.PAIRS
        self.assertGreaterEqual(len(pairs), 24)
        for symbol, (base, quote, label) in pairs.items():
            with self.subTest(symbol=symbol):
                self.assertEqual(fx.canonical(symbol), symbol)
                self.assertEqual(fx.canonical(label), symbol)
                self.assertEqual(fx.canonical(f"{base}{quote}"), symbol)
                self.assertEqual(fx.split(label), (base, quote))
                self.assertEqual(fx.resolve_key(label, pairs), symbol)
                self.assertEqual(fx.resolve_key(f"{base}{quote}", pairs), symbol)
                self.assertEqual(fx.resolve_key(symbol, pairs), symbol)

    def test_invalid_inputs_return_none_and_never_raise(self):
        for bad in ("", None, "FOO/BAR", "ZZZZZZ", "EUR/EUR", "EUR", 42, [],
                    "SPY", "^VIX", "DX-Y.NYB", "EURUSDX=Y"):
            with self.subTest(value=bad):
                self.assertIsNone(fx.canonical(bad))
                self.assertIsNone(fx.split(bad))
                self.assertIsNone(fx.label(bad))
                self.assertFalse(fx.is_known(bad))

    def test_resolve_key_unknown_returns_none(self):
        self.assertIsNone(fx.resolve_key("EUR/USD", {}))
        self.assertIsNone(fx.resolve_key("NOPE", {"EURUSD=X": ()}))


if __name__ == "__main__":
    unittest.main()
