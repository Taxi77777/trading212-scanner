"""Notation : bornes, explicabilité, et la pénalité du §21."""
import unittest

from stockscan import scoring as sc
from stockscan import structure as st
from stockscan import strength as sg

from tests import synth


def _pieces(closes, volumes=None, bench=None):
    bars = synth.build(closes, volumes)
    base = st.detect_base(bars)
    res = st.find_resistances(bars)
    top = res[0] if res else None
    return dict(
        trend=st.trend(bars),
        base=base,
        volume=st.volume_profile(bars, base),
        accum=st.accumulation(bars, top),
        comp=st.compression(bars),
        rs=sg.relative_strength(bars, synth.build(bench)) if bench else sg.RelativeStrength(),
        resistance=top,
        ext=st.extension(bars),
        price=bars.close[-1],
    )


class TestWeights(unittest.TestCase):
    def test_weights_sum_to_one_hundred(self):
        self.assertAlmostEqual(sum(sc.WEIGHTS.values()), 100.0)

    def test_every_weight_has_a_label(self):
        self.assertEqual(set(sc.WEIGHTS), set(sc.LABELS))


class TestProximity(unittest.TestCase):
    def test_just_below_resistance_scores_highest(self):
        near = st.Resistance(level=101.0, tests=3, bars_since_last_test=5,
                             source="swing", quality=8.0)
        far = st.Resistance(level=140.0, tests=3, bars_since_last_test=5,
                            source="swing", quality=8.0)
        close_score, _ = sc.proximity_score(near, 100.0)
        far_score, _ = sc.proximity_score(far, 100.0)
        self.assertGreater(close_score, far_score)
        self.assertLess(far_score, 2.0)

    def test_no_resistance_scores_zero_and_says_so(self):
        value, why = sc.proximity_score(None, 100.0)
        self.assertEqual(value, 0.0)
        self.assertTrue(why)


class TestScore(unittest.TestCase):
    def test_components_are_bounded_by_their_weight(self):
        s = sc.score(**_pieces(synth.rally_then_flat_base()))
        self.assertEqual(len(s.components), len(sc.WEIGHTS))
        for c in s.components:
            self.assertLessEqual(c.points, c.weight + 1e-9, c.key)
            self.assertGreaterEqual(c.points, 0.0, c.key)
        self.assertLessEqual(s.total, 100.0)
        self.assertGreaterEqual(s.total, 0.0)

    def test_every_component_carries_its_reason(self):
        s = sc.score(**_pieces(synth.rally_then_flat_base()))
        self.assertTrue(all(isinstance(c.why, list) for c in s.components))
        self.assertTrue(s.explain())

    def test_base_under_resistance_beats_a_downtrend(self):
        good = sc.score(**_pieces(synth.rally_then_flat_base()))
        bad = sc.score(**_pieces(synth.downtrend(300)))
        self.assertGreater(good.total, bad.total)
        self.assertGreater(good.prebreakout, bad.prebreakout)

    def test_a_stock_that_already_ran_gets_no_prebreakout_credit(self):
        closes = synth.uptrend(260, daily=0.0018)
        closes += [closes[-1] * (1 + 0.035) ** (i + 1) for i in range(12)]  # +50 % en 12 séances
        pieces = _pieces(closes)
        self.assertTrue(pieces["ext"].disqualifies_prebreakout, pieces["ext"].notes)
        s = sc.score(**pieces)
        self.assertEqual(s.prebreakout, 0.0)

    def test_sector_strength_moves_the_score_both_ways(self):
        pieces = _pieces(synth.rally_then_flat_base())
        neutral = sc.score(**pieces)
        hot = sg.RelativeStrength(rs_3m=12.0, available=True)
        cold = sg.RelativeStrength(rs_3m=-12.0, available=True)
        up = sc.score(**pieces, sector_rs=hot)
        down = sc.score(**pieces, sector_rs=cold)
        self.assertGreater(up.total, neutral.total)
        self.assertLess(down.total, neutral.total)

    def test_fundamental_bonus_is_capped(self):
        pieces = _pieces(synth.rally_then_flat_base())
        neutral = sc.score(**pieces)
        rich = sc.score(**pieces, fundamental=10.0)
        self.assertLessEqual(rich.total - neutral.total, 5.0 + 1e-9)

    def test_grade_thresholds(self):
        self.assertEqual(sc.grade_for(100.0), sc.GRADE_A)
        self.assertEqual(sc.grade_for(75.0), sc.GRADE_A)
        self.assertEqual(sc.grade_for(74.9), sc.GRADE_B)
        self.assertEqual(sc.grade_for(60.0), sc.GRADE_B)
        self.assertEqual(sc.grade_for(59.9), sc.GRADE_C)
        self.assertEqual(sc.grade_for(45.0), sc.GRADE_C)
        self.assertEqual(sc.grade_for(44.9), sc.GRADE_D)
        self.assertEqual(sc.grade_for(0.0), sc.GRADE_D)

    def test_score_uses_those_thresholds(self):
        s = sc.score(**_pieces(synth.rally_then_flat_base()))
        self.assertEqual(s.grade, sc.grade_for(s.total))

if __name__ == "__main__":
    unittest.main()
