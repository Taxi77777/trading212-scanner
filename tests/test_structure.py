import unittest

from stockscan import market_data as md
from stockscan import structure as st
from tests import synth


class TestTrend(unittest.TestCase):
    def test_uptrend_is_bullish(self):
        t = st.trend(synth.build(synth.uptrend()))
        self.assertEqual(t.direction, st.HAUSSIERE)
        self.assertTrue(t.above_ma200)
        self.assertGreaterEqual(t.score, 6)

    def test_downtrend_is_bearish(self):
        t = st.trend(synth.build(synth.downtrend()))
        self.assertEqual(t.direction, st.BAISSIERE)
        self.assertFalse(t.above_ma200)

    def test_short_history_is_neutral_not_bullish(self):
        t = st.trend(synth.build(synth.uptrend(n=40)))
        self.assertEqual(t.direction, st.NEUTRE)

    def test_none_never_raises(self):
        self.assertEqual(st.trend(None).direction, st.NEUTRE)


class TestBase(unittest.TestCase):
    def test_flat_base_after_rally_is_found(self):
        b = st.detect_base(synth.build(synth.rally_then_flat_base()))
        self.assertTrue(b.found, b.notes)
        self.assertIn(b.kind, (st.FLAT_BASE, st.RANGE, st.TRIANGLE))
        self.assertGreater(b.quality, 5)
        self.assertLess(b.depth_pct, 25)

    def test_pure_uptrend_has_no_clean_base(self):
        b = st.detect_base(synth.build(synth.uptrend(daily=0.006, wobble=0.002)))
        self.assertLess(b.quality, 11)

    def test_none_never_raises(self):
        self.assertFalse(st.detect_base(None).found)


class TestResistance(unittest.TestCase):
    def test_resistance_sits_above_price_and_is_close(self):
        bars = synth.build(synth.rally_then_flat_base())
        levels = st.find_resistances(bars)
        self.assertTrue(levels)
        top = levels[0]
        self.assertGreater(top.level, bars.close[-1])
        self.assertLess(top.distance_pct, 12)
        self.assertGreaterEqual(top.tests, 1)

    def test_levels_are_sorted_by_quality(self):
        levels = st.find_resistances(synth.build(synth.rally_then_flat_base()))
        self.assertEqual([r.quality for r in levels], sorted((r.quality for r in levels), reverse=True))

    def test_fresh_untested_high_is_not_a_resistance(self):
        """Le plus haut de la séance du jour n'est pas un niveau éprouvé."""
        levels = st.find_resistances(synth.build(synth.uptrend(daily=0.004, wobble=0.0)))
        for r in levels:
            self.assertTrue(r.bars_since_last_test >= 3 or r.tests >= 2, r)

    def test_flat_base_is_not_labelled_double_bottom(self):
        b = st.detect_base(synth.build(synth.rally_then_flat_base()))
        self.assertNotEqual(b.kind, st.DOUBLE_BOTTOM)


class TestVolumeAndAccumulation(unittest.TestCase):
    def test_constructive_volume_scores_well(self):
        closes = synth.rally_then_flat_base()
        bars = synth.build(closes, synth.volumes_for(closes, dry_from=200))
        base = st.detect_base(bars)
        vp = st.volume_profile(bars, base)
        self.assertGreater(vp.score, 6, vp.notes)
        self.assertGreater(vp.up_down_ratio, 1.0)

    def test_obv_rising_under_resistance_is_accumulation(self):
        closes = synth.rally_then_flat_base()
        bars = synth.build(closes, synth.volumes_for(closes))
        res = st.find_resistances(bars)[0]
        acc = st.accumulation(bars, res)
        self.assertGreater(acc.score, 2, acc.notes)

    def test_distribution_is_penalised(self):
        closes = synth.uptrend(daily=0.004)
        vols = [1_000_000 * (0.6 if closes[i] > closes[i - 1] else 1.6) for i in range(len(closes))]
        vols[0] = 1_000_000
        acc = st.accumulation(synth.build(closes, vols))
        self.assertLess(acc.score, 6)


class TestCompressionAndExtension(unittest.TestCase):
    def test_compression_detected_in_tightening_base(self):
        closes = synth.rally_then_flat_base(base=70, tight=0.004)
        c = st.compression(synth.build(closes))
        self.assertTrue(c.detected, c.notes)
        self.assertLess(c.ratio, 1.0)

    def test_no_compression_in_volatile_trend(self):
        c = st.compression(synth.build(synth.uptrend(daily=0.004, wobble=0.05)))
        self.assertLessEqual(c.score, 3)

    def test_extension_penalises_a_stock_that_already_ran(self):
        closes = synth.uptrend(n=380) + [200 * (1 + 0.05 * i) for i in range(1, 21)]
        e = st.extension(synth.build(closes))
        self.assertTrue(e.disqualifies_prebreakout, e.notes)
        self.assertGreaterEqual(e.penalty, 20)

    def test_quiet_stock_has_no_penalty(self):
        e = st.extension(synth.build(synth.rally_then_flat_base()))
        self.assertLess(e.penalty, 5)


if __name__ == "__main__":
    unittest.main()
