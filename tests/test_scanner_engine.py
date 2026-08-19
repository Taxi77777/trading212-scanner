import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import forex_intraday_scanner_v3 as scanner
from tests import synthetic

# The engine refuses to build outside a trading session. Pin it so the tests are
# deterministic whatever the clock says on the runner.
_REAL_SESSION = scanner.session_name


_REAL_THRESHOLDS = (scanner.SETUP_MIN, scanner.FINAL_MIN)


def setUpModule():
    scanner.session_name = lambda: "LONDRES + NEW YORK"
    # Pin the thresholds too: importing a runner (v4/v5/v6/v7) mutates them
    # globally, which would otherwise make these tests order-dependent.
    scanner.SETUP_MIN, scanner.FINAL_MIN = 60, 68


def tearDownModule():
    scanner.session_name = _REAL_SESSION
    scanner.SETUP_MIN, scanner.FINAL_MIN = _REAL_THRESHOLDS


class TestPairKey(unittest.TestCase):
    def test_all_spellings_resolve(self):
        for label in ("EUR/USD", "USD/CHF", "AUD/CHF", "CAD/CHF", "GBP/USD", "USD/JPY"):
            base, quote = label.split("/")
            symbol = f"{base}{quote}=X"
            if symbol not in scanner.PAIRS:
                continue
            for spelling in (label, f"{base}{quote}", symbol, label.lower()):
                self.assertEqual(scanner.pair_key(spelling), symbol, spelling)

    def test_unknown_pair_is_returned_untouched(self):
        self.assertEqual(scanner.pair_key("NOT/APAIR"), "NOT/APAIR")


class TestEventRisk(unittest.TestCase):
    """The KeyError: 'EUR/USD' regression, covered for every spelling."""

    def setUp(self):
        self.now = time.time()
        self.events = [
            {"impact": "High", "currency": "USD", "timestamp": self.now + 600},
            {"impact": "Low", "currency": "EUR", "timestamp": self.now + 600},
            {"impact": "High", "currency": "JPY", "timestamp": self.now + 99999},
        ]

    def test_label_symbol_and_bare_agree(self):
        for label in ("EUR/USD", "USD/CHF", "AUD/CHF", "CAD/CHF", "GBP/USD", "USD/JPY"):
            base, quote = label.split("/")
            symbol = f"{base}{quote}=X"
            results = {scanner.event_risk(spelling, self.events)
                       for spelling in (label, f"{base}{quote}", symbol)}
            self.assertEqual(len(results), 1, f"{label} incohérent: {results}")

    def test_usd_pairs_are_blocked_by_usd_high_impact(self):
        for label in ("EUR/USD", "USD/CHF", "GBP/USD", "USD/JPY"):
            _, blocked = scanner.event_risk(label, self.events)
            self.assertTrue(blocked, label)

    def test_non_usd_pairs_are_not_blocked(self):
        for label in ("AUD/CHF", "CAD/CHF"):
            _, blocked = scanner.event_risk(label, self.events)
            self.assertFalse(blocked, label)

    def test_far_future_event_does_not_block(self):
        _, blocked = scanner.event_risk("EUR/JPY", self.events)
        self.assertFalse(blocked)

    def test_unknown_pair_never_raises(self):
        label, blocked = scanner.event_risk("NOT/APAIR", self.events)
        self.assertFalse(blocked)
        self.assertIn("INCONNUE", label)

    def test_malformed_events_never_raise(self):
        junk = [{}, {"impact": "High"}, {"impact": "High", "currency": "USD"},
                {"impact": "High", "currency": "USD", "timestamp": "not-a-date"}, None]
        for pair in ("EUR/USD", "EURUSD=X"):
            _, blocked = scanner.event_risk(pair, [e for e in junk if e is not None])
            self.assertFalse(blocked)


class TestCorrelationSideAwareness(unittest.TestCase):
    def test_label_flips_with_side(self):
        frames = synthetic.market(dxy_direction=1)   # strong USD
        buy_label, buy_score = scanner.correlation_bias("USDCHF=X", frames, "BUY")
        sell_label, sell_score = scanner.correlation_bias("USDCHF=X", frames, "SELL")
        self.assertEqual(buy_score, sell_score)
        self.assertNotEqual(buy_label, sell_label)
        self.assertEqual(buy_label, "CONFIRMÉ")
        self.assertEqual(sell_label, "CONTRE")

    def test_unknown_pair_is_neutral(self):
        self.assertEqual(scanner.correlation_bias("NOT/APAIR", {}), ("NEUTRE", 0))


class TestBuildSignal(unittest.TestCase):
    def _frames(self, direction=1, dxy=-1):
        data = synthetic.frames(direction)
        data.update(synthetic.market(dxy))
        return data

    def test_bullish_series_produces_a_buy(self):
        scanner.diag_reset()
        frames = self._frames(direction=1, dxy=-1)
        strength = {"EUR": 2.0, "USD": -1.0, "GBP": 0.0, "JPY": 0.0,
                    "CHF": 0.0, "AUD": 0.0, "NZD": 0.0, "CAD": 0.0}
        sig = scanner.build_signal("EURUSD=X", frames, strength, "MIXTE", "", "AUCUN", False)
        self.assertIsNotNone(sig, f"diagnostic={scanner.DIAG}")
        self.assertEqual(sig.side, "BUY")
        self.assertEqual(sig.pair, "EUR/USD")
        self.assertEqual(sig.symbol, "EURUSD=X")
        self.assertIn(sig.state, ("SETUP", "ENTREE"))
        self.assertLess(sig.sl, sig.price)
        self.assertGreater(sig.tp1, sig.price)
        self.assertGreater(sig.tp2, sig.tp1)

    def test_bearish_series_produces_a_sell(self):
        scanner.diag_reset()
        frames = self._frames(direction=-1, dxy=1)
        strength = {"EUR": -2.0, "USD": 1.0, "GBP": 0.0, "JPY": 0.0,
                    "CHF": 0.0, "AUD": 0.0, "NZD": 0.0, "CAD": 0.0}
        sig = scanner.build_signal("EURUSD=X", frames, strength, "MIXTE", "", "AUCUN", False)
        self.assertIsNotNone(sig, f"diagnostic={scanner.DIAG}")
        self.assertEqual(sig.side, "SELL")
        self.assertGreater(sig.sl, sig.price)
        self.assertLess(sig.tp1, sig.price)

    def test_reward_risk_matches_the_actual_stop(self):
        scanner.diag_reset()
        strength = {"EUR": 2.0, "USD": -1.0, "GBP": 0.0, "JPY": 0.0,
                    "CHF": 0.0, "AUD": 0.0, "NZD": 0.0, "CAD": 0.0}
        sig = scanner.build_signal("EURUSD=X", self._frames(1, -1), strength,
                                   "MIXTE", "", "AUCUN", False)
        self.assertIsNotNone(sig, f"diagnostic={scanner.DIAG}")
        expected = abs(sig.tp1 - sig.price) / abs(sig.price - sig.sl)
        self.assertAlmostEqual(sig.rr, round(expected, 2), places=2)
        self.assertGreater(sig.rr, 0)

    def test_news_block_is_diagnosed_not_silent(self):
        scanner.diag_reset()
        sig = scanner.build_signal(
            "EURUSD=X", self._frames(1, -1),
            {"EUR": 2.0, "USD": -1.0, "GBP": 0, "JPY": 0, "CHF": 0, "AUD": 0, "NZD": 0, "CAD": 0},
            "MIXTE", "", "HIGH IMPACT USD < 30m", True)
        self.assertIsNone(sig)
        self.assertEqual(scanner.DIAG.get("news_bloquante"), 1)

    def test_missing_timeframe_is_diagnosed(self):
        scanner.diag_reset()
        frames = self._frames(1, -1)
        frames["m15"] = None
        sig = scanner.build_signal(
            "EURUSD=X", frames,
            {c: 0.0 for c in ("EUR", "USD", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD")},
            "MIXTE", "", "AUCUN", False)
        self.assertIsNone(sig)
        self.assertTrue(any(k.startswith("donnees_insuffisantes") for k in scanner.DIAG))

    def test_flat_market_yields_no_signal(self):
        """Zero signal must be a valid, diagnosable outcome."""
        scanner.diag_reset()
        flat = {
            "d1": synthetic.series(300, 1.0, 0.0, 86400, wobble=0.0001),
            "h1": synthetic.series(1200, 1.0, 0.0, 3600, wobble=0.0001),
            "m15": synthetic.series(400, 1.0, 0.0, 900, wobble=0.0001),
        }
        flat["h4"] = scanner.resample_h4(flat["h1"])
        flat.update(synthetic.market(0))
        sig = scanner.build_signal(
            "EURUSD=X", flat,
            {c: 0.0 for c in ("EUR", "USD", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD")},
            "MIXTE", "", "AUCUN", False)
        self.assertIsNone(sig)
        self.assertEqual(scanner.DIAG.get("score_sous_seuil_setup"), 1,
                         f"diagnostic inattendu: {scanner.DIAG}")


class TestVolatilityRegimePerformance(unittest.TestCase):
    def test_is_linear_and_stable(self):
        bars = synthetic.series(2000, 1.0, 0.0002, 900, wobble=0.0003)
        start = time.time()
        regime = scanner.volatility_regime(bars)
        elapsed = time.time() - start
        self.assertIn(regime, ("INCONNU", "FAIBLE", "NORMALE", "ELEVEE", "EXPLOSIVE"))
        self.assertLess(elapsed, 0.5, f"volatility_regime trop lent: {elapsed:.2f}s")

    def test_short_series_is_unknown(self):
        self.assertEqual(scanner.volatility_regime(None), "INCONNU")
        self.assertEqual(
            scanner.volatility_regime(synthetic.series(10, 1.0, 0.001, 900)), "INCONNU")


class TestFormatting(unittest.TestCase):
    def _signal(self):
        scanner.diag_reset()
        frames = synthetic.frames(1)
        frames.update(synthetic.market(-1))
        return scanner.build_signal(
            "EURUSD=X", frames,
            {"EUR": 2.0, "USD": -1.0, "GBP": 0, "JPY": 0, "CHF": 0, "AUD": 0, "NZD": 0, "CAD": 0},
            "MIXTE", "", "AUCUN HIGH IMPACT", False)

    def test_message_contains_every_required_field(self):
        sig = self._signal()
        self.assertIsNotNone(sig)
        sig.medal_label = "🥇 OR"
        sig.quality_score = 82
        sig.ai_verdict = "CONFIRME"
        sig.ai_confidence = 91
        sig.ai_reason = "Toutes les unités de temps alignées"
        text = scanner.format_signal(sig)
        for expected in ("🥇 OR — SIGNAL FOREX", "STRATÉGIE : D1 + H4 + H1 + M15",
                         "Direction : ACHAT", "Score :", "Qualité globale : 82/100",
                         "Entrée :", "SL :", "TP1 :", "TP2 :", "R:R TP1 : 1:",
                         "D1 :", "H4 :", "H1 :", "M15 :", "DXY :",
                         "Force multi-horizon :", "Volatilité :", "Liquidité :",
                         "Corrélation :", "Macro :", "Session :", "News :",
                         "🤖 IA Cloudflare Qwen3 : CONFIRME (91%)", "Motif IA :",
                         "Confluence :",
                         "⚠️ Analyse uniquement — aucun ordre Forex n'est exécuté."):
            self.assertIn(expected, text, expected)

    def test_offline_ai_is_stated_not_faked(self):
        sig = self._signal()
        sig.ai_verdict = "INDISPONIBLE"
        text = scanner.format_signal(sig)
        self.assertIn("🤖 IA Cloudflare Qwen3 : INDISPONIBLE", text)
        self.assertNotIn("(0%)", text)

    def test_jpy_pairs_use_three_decimals(self):
        self.assertEqual(scanner.price_fmt("USD/JPY", 147.123456), "147.123")
        self.assertEqual(scanner.price_fmt("EUR/USD", 1.123456), "1.12346")
        self.assertEqual(scanner.price_fmt("USDJPY=X", 147.123456), "147.123")


class TestStatePruning(unittest.TestCase):
    def test_old_entries_are_dropped_and_others_kept(self):
        now = time.time()
        state = {
            "FXV3:EUR/USD:BUY:SETUP": {"sent_at": now},
            "FXV3:GBP/USD:BUY:SETUP": {"sent_at": now - 400 * 86400},
            "FXV3:broken": "not-a-dict",
            "OTHER:key": {"sent_at": 0},
        }
        pruned = scanner.prune_state(state)
        self.assertIn("FXV3:EUR/USD:BUY:SETUP", pruned)
        self.assertNotIn("FXV3:GBP/USD:BUY:SETUP", pruned)
        self.assertNotIn("FXV3:broken", pruned)
        self.assertIn("OTHER:key", pruned)


if __name__ == "__main__":
    unittest.main()


class TestNoHumanLabelDependency(unittest.TestCase):
    """No entry point may require the technical key; none may crash on a label."""

    def _args(self):
        data = synthetic.frames(1)
        data.update(synthetic.market(-1))
        strength = {"EUR": 2.0, "USD": -1.0, "GBP": 0.0, "JPY": 0.0,
                    "CHF": 0.0, "AUD": 0.0, "NZD": 0.0, "CAD": 0.0}
        return data, strength

    def test_build_signal_accepts_every_spelling(self):
        frames, strength = self._args()
        results = []
        for spelling in ("EURUSD=X", "EUR/USD", "EURUSD", "eur/usd"):
            scanner.diag_reset()
            sig = scanner.build_signal(spelling, frames, strength, "MIXTE", "", "AUCUN", False)
            self.assertIsNotNone(sig, spelling)
            results.append((sig.pair, sig.symbol, sig.side, round(sig.score)))
        self.assertEqual(len(set(results)), 1, f"résultats divergents: {results}")

    def test_build_signal_rejects_an_unknown_pair_without_raising(self):
        frames, strength = self._args()
        scanner.diag_reset()
        self.assertIsNone(
            scanner.build_signal("NOT/APAIR", frames, strength, "MIXTE", "", "AUCUN", False))
        self.assertEqual(scanner.DIAG.get("paire_inconnue"), 1)

    def test_rate_overlay_accepts_every_spelling(self):
        import central_bank_rates as cb
        rates = {"EUR": 4.0, "USD": 2.0}
        expected = cb.assessment("EUR/USD", "BUY", rates)
        for spelling in ("EURUSD=X", "EURUSD", "eur/usd"):
            self.assertEqual(cb.assessment(spelling, "BUY", rates), expected, spelling)


class TestSuiteIsHermetic(unittest.TestCase):
    def test_rate_overlay_is_neutralised(self):
        import run_forex_v4

        self.assertEqual(run_forex_v4.rates(), {},
                         "les tests ne doivent jamais dépendre des taux réels")
        self.assertIn("hors ligne", run_forex_v4.rate_source())

    def test_rate_overlay_is_neutral_when_rates_are_unknown(self):
        import central_bank_rates as cb

        self.assertEqual(cb.assessment("EUR/USD", "BUY", {}), ("TAUX_INCONNU", None))
