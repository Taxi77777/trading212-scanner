"""Force relative et régime de marché."""
import unittest

from stockscan import strength as sg
from stockscan.market_data import Bars

from tests import synth


def _bars(closes, t0=synth.T0):
    return synth.build(closes, t0=t0)


class TestAlign(unittest.TestCase):
    def test_aligns_on_calendar_day_not_position(self):
        stock = _bars(synth.uptrend(200))
        # L'indice a 20 séances de plus AVANT : un alignement par position
        # comparerait la séance 0 de l'action à la séance 0 de l'indice.
        bench = _bars(synth.uptrend(220), t0=synth.T0 - 20 * synth.DAY)
        px, bx = sg.align(stock, bench)
        self.assertEqual(len(px), 200)
        self.assertEqual(len(bx), 200)

    def test_missing_sessions_are_dropped_not_shifted(self):
        stock = _bars(synth.uptrend(100))
        bench = _bars(synth.uptrend(100))
        holes = Bars([t for i, t in enumerate(bench.ts) if i % 7],
                     [v for i, v in enumerate(bench.open) if i % 7],
                     [v for i, v in enumerate(bench.high) if i % 7],
                     [v for i, v in enumerate(bench.low) if i % 7],
                     [v for i, v in enumerate(bench.close) if i % 7],
                     [v for i, v in enumerate(bench.volume) if i % 7])
        px, bx = sg.align(stock, holes)
        self.assertEqual(len(px), len(bx))
        self.assertLess(len(px), 100)

    def test_none_never_raises(self):
        self.assertEqual(sg.align(None, None), ([], []))
        self.assertFalse(sg.relative_strength(None, None).available)


class TestRelativeStrength(unittest.TestCase):
    def test_leader_beats_laggard(self):
        bench = _bars(synth.uptrend(300, daily=0.0008))
        leader = _bars(synth.uptrend(300, daily=0.0025))
        laggard = _bars(synth.uptrend(300, daily=0.0001))
        strong = sg.relative_strength(leader, bench)
        weak = sg.relative_strength(laggard, bench)
        self.assertGreater(strong.score, weak.score)
        self.assertGreater(strong.rs_3m, 0)
        self.assertLess(weak.rs_3m, 0)

    def test_rs_line_new_high_is_flagged(self):
        bench = _bars(synth.uptrend(300, daily=0.0005, wobble=0.002))
        leader = _bars(synth.uptrend(300, daily=0.0030, wobble=0.002))
        rs = sg.relative_strength(leader, bench)
        self.assertTrue(rs.line_new_high, rs.notes)
        self.assertTrue(rs.line_rising)

    def test_score_is_bounded(self):
        bench = _bars(synth.uptrend(300, daily=0.0001))
        leader = _bars(synth.uptrend(300, daily=0.02))
        rs = sg.relative_strength(leader, bench)
        self.assertLessEqual(rs.score, 15.0)
        self.assertGreaterEqual(rs.score, 0.0)


class TestRegime(unittest.TestCase):
    def test_healthy_index_is_risk_on(self):
        reg = sg.market_regime(_bars(synth.uptrend(400)))
        self.assertEqual(reg.label, sg.RISK_ON, reg.notes)
        self.assertTrue(reg.allow_new_positions)
        self.assertEqual(reg.size_multiplier, 1.0)

    def test_broken_index_is_risk_off(self):
        reg = sg.market_regime(_bars(synth.downtrend(400)))
        self.assertEqual(reg.label, sg.RISK_OFF, reg.notes)
        self.assertLess(reg.size_multiplier, 1.0)

    def test_bad_breadth_drags_the_score_down(self):
        bars = _bars(synth.uptrend(400))
        wide = sg.market_regime(bars, breadth_pct=75.0)
        narrow = sg.market_regime(bars, breadth_pct=20.0)
        self.assertGreater(wide.score, narrow.score)

    def test_high_vix_drags_the_score_down(self):
        bars = _bars(synth.uptrend(400))
        calm = sg.market_regime(bars, vix_bars=_bars([14.0] * 40))
        panic = sg.market_regime(bars, vix_bars=_bars([38.0] * 40))
        self.assertGreater(calm.score, panic.score)
        self.assertEqual(panic.vix, 38.0)

    def test_missing_index_is_neutral_not_optimistic(self):
        reg = sg.market_regime(None)
        self.assertEqual(reg.label, sg.NEUTRE)
        self.assertEqual(reg.score, 50.0)

    def test_breadth_helper(self):
        self.assertIsNone(sg.breadth(0, 0))
        self.assertAlmostEqual(sg.breadth(30, 120), 25.0)


if __name__ == "__main__":
    unittest.main()
