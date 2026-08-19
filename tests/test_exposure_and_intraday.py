"""Regression tests for the 19/08/2026 CHF cluster.

Six alerts that morning were the same bet — short CHF — and all six lost, while
the two signals expressing a different bet (short USD) both reached TP1. Two
causes, both covered here:

* the engine ranked CHF *weakest* on daily returns while CHF was already the
  2nd strongest currency intraday;
* nothing prevented one view from being broadcast as six independent alerts.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import forex_quality as q
import run_forex_v7 as v7


class Sig:
    def __init__(self, pair, side="BUY", score=70, **kw):
        base, quote = pair.split("/")
        # Structure and strength follow the side, so a SELL fixture is a
        # coherent short rather than a long with every factor inverted.
        bias = "BULLISH" if side == "BUY" else "BEARISH"
        delta = "+1.4" if side == "BUY" else "-1.4"
        defaults = dict(
            pair=pair, symbol=f"{base}{quote}=X", side=side, state="ENTREE", score=score,
            d1=bias, h4=bias, h1=bias, m15="CONFIRME",
            dxy="NEUTRAL", macro="MIXTE", strength=f"{base} {delta} vs {quote}",
            strength_intraday="", vol_regime="NORMALE", liquidity="RANGE",
            correlation="NEUTRE", news="AUCUN HIGH IMPACT CONFIGURE",
            session="LONDRES", reasons=[], rr=1.7, regime_intraday="",
            ai_verdict="INDISPONIBLE", ai_confidence=0,
        )
        defaults.update(kw)
        for k, v in defaults.items():
            setattr(self, k, v)


class TestIntradayStrength(unittest.TestCase):
    def test_aligned_intraday_strength_is_a_plus(self):
        result = q.coherence(Sig("CAD/CHF", strength_intraday="CAD +0.31% vs CHF (4-24h)"))
        self.assertIn("force intraday alignée", result["pro"])

    def test_cadchf_19_08_is_now_penalised(self):
        """Real values at 08:00 UTC: engine said CAD 3rd / CHF 8th; the market
        had CHF 2nd and CAD 7th over the previous 24 h."""
        sig = Sig("CAD/CHF", side="BUY", score=73,
                  strength="CAD +2.6 vs CHF",              # moteur, D1 5/20/60j
                  strength_intraday="CAD -0.37% vs CHF (4-24h)")  # réel intraday
        result = q.coherence(sig)
        self.assertIn("force intraday opposée", result["contra"])
        self.assertIn("divergence force journalière / intraday", result["contra"])
        self.assertLess(result["score"], 70, f"cohérence trop clémente: {result}")

    def test_divergence_needs_both_horizons_to_disagree(self):
        agree = q.coherence(Sig("CAD/CHF", strength="CAD +2.6 vs CHF",
                                strength_intraday="CAD +0.31% vs CHF (4-24h)"))
        self.assertNotIn("divergence force journalière / intraday", agree["contra"])

    def test_missing_intraday_data_is_neutral(self):
        result = q.coherence(Sig("CAD/CHF", strength_intraday=""))
        self.assertNotIn("force intraday opposée", result["contra"])
        self.assertNotIn("force intraday alignée", result["pro"])

    def test_flat_intraday_is_ignored(self):
        result = q.coherence(Sig("CAD/CHF", strength_intraday="CAD +0.01% vs CHF (4-24h)"))
        self.assertNotIn("force intraday alignée", result["pro"])


class TestCurrencyExposure(unittest.TestCase):
    """The cap is set explicitly in each test: these assertions are about the
    filter's behaviour, not about whichever default is currently shipped."""

    def setUp(self):
        self._saved = v7.MAX_PER_CURRENCY
        v7.MAX_PER_CURRENCY = 1
        self.addCleanup(lambda: setattr(v7, "MAX_PER_CURRENCY", self._saved))

    @staticmethod
    def _pack(sigs):
        return [(s, q.coherence(s)) for s in sigs]

    def test_shipped_default_allows_two_expressions_of_one_view(self):
        v7.MAX_PER_CURRENCY = self._saved
        self.assertEqual(self._saved, 2)
        cluster = [Sig(p, score=sc) for p, sc in
                   (("AUD/CHF", 75), ("CAD/CHF", 73), ("EUR/CHF", 70), ("GBP/CHF", 70))]
        kept = v7._limit_currency_exposure(self._pack(cluster))
        self.assertEqual(len(kept), 2, [s.pair for s, _ in kept])

    def test_repeated_chf_short_is_collapsed_to_one(self):
        cluster = [Sig(p, score=sc) for p, sc in
                   (("AUD/CHF", 75), ("CAD/CHF", 73), ("EUR/CHF", 70),
                    ("GBP/CHF", 70), ("USD/CHF", 70), ("NZD/CHF", 55))]
        kept = v7._limit_currency_exposure(self._pack(cluster))
        self.assertEqual(len(kept), 1, [s.pair for s, _ in kept])
        self.assertEqual(kept[0][0].pair, "AUD/CHF", "le mieux classé doit survivre")

    def test_different_bets_are_all_kept(self):
        mixed = [Sig("AUD/CHF", score=75), Sig("GBP/USD", score=63), Sig("EUR/JPY", score=56)]
        kept = v7._limit_currency_exposure(self._pack(mixed))
        self.assertEqual({s.pair for s, _ in kept}, {"AUD/CHF", "GBP/USD", "EUR/JPY"})

    def test_opposite_directions_on_one_currency_are_not_confused(self):
        both = [Sig("GBP/USD", side="BUY", score=63), Sig("USD/CHF", side="BUY", score=62)]
        kept = v7._limit_currency_exposure(self._pack(both))
        self.assertEqual(len(kept), 2, "short USD et long USD sont deux paris distincts")

    def test_19_08_cluster_lets_the_winners_through(self):
        """The six CHF shorts crowded out GBP/USD and AUD/USD, which both hit TP1."""
        day = [Sig(p, side=sd, score=sc) for p, sd, sc in (
            ("AUD/CHF", "BUY", 75), ("CAD/CHF", "BUY", 73), ("EUR/CHF", "BUY", 70),
            ("GBP/CHF", "BUY", 70), ("USD/CHF", "BUY", 70), ("GBP/USD", "BUY", 63),
            ("AUD/USD", "BUY", 62), ("NZD/CHF", "BUY", 55))]
        kept = [s.pair for s, _ in v7._limit_currency_exposure(self._pack(day))]
        self.assertIn("GBP/USD", kept, f"le gagnant est encore écarté: {kept}")
        self.assertEqual(sum(1 for p in kept if p.endswith("/CHF")), 1,
                         f"cluster CHF non résorbé: {kept}")

    def test_filter_is_disablable(self):
        v7.MAX_PER_CURRENCY = 0
        try:
            cluster = [Sig("AUD/CHF"), Sig("CAD/CHF"), Sig("EUR/CHF")]
            self.assertEqual(len(v7._limit_currency_exposure(self._pack(cluster))), 3)
        finally:
            pass

    def test_unknown_pair_is_not_dropped(self):
        s = Sig("EUR/USD"); s.symbol = "???"; s.pair = "???"
        self.assertEqual(len(v7._limit_currency_exposure(self._pack([s]))), 1)


if __name__ == "__main__":
    unittest.main()


class TestIntradayRiskRegime(unittest.TestCase):
    """19/08/2026: ten alerts sold a safe haven into an intraday risk-off move.
    All ten lost, for a combined -22.72 R."""

    def setUp(self):
        import forex_intraday_scanner_v3 as scanner
        self.scanner = scanner

    def test_regime_detects_bid_havens(self):
        label, score = self.scanner.intraday_risk_regime(
            {"JPY": 0.22, "CHF": 0.08, "AUD": -0.27, "NZD": -0.05, "CAD": -0.10})
        self.assertEqual(label, "RISK-OFF INTRADAY")
        self.assertGreater(score, 0.10)

    def test_regime_detects_sold_havens(self):
        label, _ = self.scanner.intraday_risk_regime(
            {"JPY": -0.30, "CHF": -0.20, "AUD": 0.25, "NZD": 0.20, "CAD": 0.15})
        self.assertEqual(label, "RISK-ON INTRADAY")

    def test_small_spread_stays_neutral(self):
        label, _ = self.scanner.intraday_risk_regime(
            {"JPY": 0.02, "CHF": 0.01, "AUD": -0.01, "NZD": 0.0, "CAD": -0.02})
        self.assertEqual(label, "NEUTRE INTRADAY")

    def test_missing_data_is_unknown(self):
        self.assertEqual(self.scanner.intraday_risk_regime({})[0], "INCONNU")
        self.assertEqual(self.scanner.intraday_risk_regime({"JPY": 0.2})[0], "INCONNU")

    def test_haven_leg_detection(self):
        self.assertEqual(self.scanner.haven_leg("CAD/CHF", "BUY"), ("CHF", None))
        self.assertEqual(self.scanner.haven_leg("USD/JPY", "BUY"), ("JPY", None))
        self.assertEqual(self.scanner.haven_leg("EUR/JPY", "SELL"), (None, "JPY"))
        self.assertEqual(self.scanner.haven_leg("GBP/USD", "BUY"), (None, None))
        self.assertEqual(self.scanner.haven_leg("CHF/JPY", "BUY"), ("JPY", "CHF"))


class TestHavenVeto(unittest.TestCase):
    REGIME_OFF = "RISK-OFF INTRADAY (+0.25)"
    REGIME_ON = "RISK-ON INTRADAY (-0.25)"

    def test_selling_a_haven_in_risk_off_is_vetoed(self):
        for pair in ("CAD/CHF", "AUD/CHF", "EUR/CHF", "GBP/CHF", "USD/CHF",
                     "NZD/CHF", "EUR/JPY", "CAD/JPY", "GBP/JPY", "USD/JPY"):
            with self.subTest(pair=pair):
                result = q.coherence(Sig(pair, side="BUY", regime_intraday=self.REGIME_OFF))
                self.assertEqual(result["verdict"], q.INCOHERENT)
                self.assertIn("risk-off", result["veto"])

    def test_non_haven_trades_pass(self):
        for pair, side in (("GBP/USD", "BUY"), ("AUD/USD", "BUY"),
                           ("EUR/GBP", "SELL"), ("NZD/CAD", "SELL"), ("EUR/AUD", "SELL")):
            with self.subTest(pair=pair):
                result = q.coherence(Sig(pair, side=side, regime_intraday=self.REGIME_OFF))
                self.assertNotEqual(result["verdict"], q.INCOHERENT)
                self.assertIsNone(result.get("veto"))

    def test_buying_a_haven_in_risk_off_is_allowed(self):
        result = q.coherence(Sig("CAD/CHF", side="SELL", regime_intraday=self.REGIME_OFF))
        self.assertIsNone(result.get("veto"))

    def test_buying_a_haven_in_risk_on_is_vetoed(self):
        result = q.coherence(Sig("CAD/CHF", side="SELL", regime_intraday=self.REGIME_ON))
        self.assertEqual(result["verdict"], q.INCOHERENT)
        self.assertIn("risk-on", result["veto"])

    def test_haven_versus_haven_is_exempt(self):
        for side in ("BUY", "SELL"):
            result = q.coherence(Sig("CHF/JPY", side=side, regime_intraday=self.REGIME_OFF))
            self.assertIsNone(result.get("veto"), side)

    def test_neutral_or_unknown_regime_never_vetoes(self):
        for regime in ("NEUTRE INTRADAY (+0.02)", "INCONNU (0.00)", ""):
            result = q.coherence(Sig("CAD/CHF", side="BUY", regime_intraday=regime))
            self.assertIsNone(result.get("veto"), regime)

    def test_veto_is_disablable(self):
        saved = q.REGIME_VETO
        q.REGIME_VETO = False
        try:
            result = q.coherence(Sig("CAD/CHF", side="BUY", regime_intraday=self.REGIME_OFF))
            self.assertIsNone(result.get("veto"))
            self.assertIn("vend CHF en plein risk-off intraday", result["contra"])
        finally:
            q.REGIME_VETO = saved
