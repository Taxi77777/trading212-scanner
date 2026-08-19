import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import forex_quality as q


class Sig:
    """Minimal stand-in for FxSignal."""

    def __init__(self, **kw):
        defaults = dict(
            pair="EUR/USD", symbol="EURUSD=X", side="BUY", state="SETUP", score=70,
            d1="BULLISH", h4="BULLISH", h1="BULLISH", m15="CONFIRME",
            dxy="NEUTRAL", macro="MIXTE", strength="EUR +1.4 vs USD",
            vol_regime="NORMALE", liquidity="RANGE", correlation="NEUTRE",
            news="AUCUN HIGH IMPACT CONFIGURE", session="LONDRES", reasons=[],
            rr=1.7, ai_verdict="INDISPONIBLE", ai_confidence=0,
        )
        defaults.update(kw)
        for key, value in defaults.items():
            setattr(self, key, value)


class TestCoherence(unittest.TestCase):
    def test_fully_aligned_is_coherent(self):
        result = q.coherence(Sig())
        self.assertEqual(result["verdict"], q.COHERENT)
        self.assertGreaterEqual(result["score"], 80)

    def test_usdchf_buy_with_bearish_dxy_is_not_auto_rejected(self):
        """Explicit requirement: DXY alone must never veto a signal."""
        sig = Sig(pair="USD/CHF", symbol="USDCHF=X", side="BUY", dxy="BEAR",
                  strength="USD +1.8 vs CHF")
        result = q.coherence(sig)
        self.assertNotEqual(result["verdict"], q.INCOHERENT)
        self.assertIn("DXY BEAR opposé", result["contra"])

    def test_many_independent_contradictions_do_reject(self):
        sig = Sig(pair="USD/CHF", symbol="USDCHF=X", side="BUY", dxy="BEAR",
                  d1="BEARISH", h4="BEARISH", h1="BEARISH", m15="EN_ATTENTE",
                  strength="USD -2.1 vs CHF", correlation="CONTRE",
                  vol_regime="EXPLOSIVE", rr=1.0)
        result = q.coherence(sig)
        self.assertEqual(result["verdict"], q.INCOHERENT)
        self.assertGreaterEqual(len(result["contra"]), 3)

    def test_sell_side_mirrors_buy_side(self):
        buy = q.coherence(Sig(side="BUY", d1="BULLISH", h4="BULLISH", h1="BULLISH"))
        sell = q.coherence(Sig(side="SELL", d1="BEARISH", h4="BEARISH", h1="BEARISH",
                               strength="EUR -1.4 vs USD"))
        self.assertEqual(buy["verdict"], sell["verdict"])

    def test_high_impact_news_counts_against(self):
        result = q.coherence(Sig(news="HIGH IMPACT USD < 30m"))
        self.assertIn("news high impact imminente", result["contra"])

    def test_never_raises_on_empty_signal(self):
        class Empty:
            pass
        result = q.coherence(Empty())
        self.assertIn(result["verdict"], (q.COHERENT, q.MITIGE, q.INCOHERENT))


class TestQualityAndMedals(unittest.TestCase):
    def test_ai_confirmation_beats_ai_prudence(self):
        confirmed = q.quality(Sig(ai_verdict="CONFIRME", ai_confidence=92))["quality"]
        prudent = q.quality(Sig(ai_verdict="PRUDENCE", ai_confidence=40))["quality"]
        self.assertGreater(confirmed, prudent)

    def test_offline_ai_is_neutral_not_punitive(self):
        offline = q.quality(Sig(ai_verdict="INDISPONIBLE"))["quality"]
        prudent = q.quality(Sig(ai_verdict="PRUDENCE"))["quality"]
        self.assertGreaterEqual(offline, prudent)

    def test_quality_is_not_the_raw_score(self):
        """Same engine score, different context => different quality."""
        strong = q.quality(Sig(score=70, m15="CONFIRME", rr=2.4,
                               liquidity="SWEEP_LOW_RECLAIM",
                               ai_verdict="CONFIRME", ai_confidence=90))["quality"]
        weak = q.quality(Sig(score=70, m15="EN_ATTENTE", rr=1.1,
                             d1="BEARISH", correlation="CONTRE",
                             vol_regime="EXPLOSIVE"))["quality"]
        self.assertGreater(strong, weak)
        self.assertGreater(strong - weak, 10)

    def test_m15_confirmation_raises_quality(self):
        with_m15 = q.quality(Sig(m15="CONFIRME"))["quality"]
        without = q.quality(Sig(m15="EN_ATTENTE"))["quality"]
        self.assertGreater(with_m15, without)

    def test_medal_thresholds(self):
        self.assertEqual(q.medal(90), ("🥇", "OR"))
        self.assertEqual(q.medal(78), ("🥇", "OR"))
        self.assertEqual(q.medal(70), ("🥈", "ARGENT"))
        self.assertEqual(q.medal(60), ("🥉", "BRONZE"))
        self.assertEqual(q.medal(40), ("", ""))

    def test_quality_stays_within_bounds(self):
        for score in (0, 50, 100, 250, -10):
            value = q.quality(Sig(score=score))["quality"]
            self.assertGreaterEqual(value, 0)
            self.assertLessEqual(value, 100)


if __name__ == "__main__":
    unittest.main()
