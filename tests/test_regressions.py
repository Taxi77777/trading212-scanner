"""Non-régressions : un test par défaut trouvé en revue adversariale.

Chaque test décrit le défaut d'origine, pas seulement le comportement attendu.
Sans cela, un futur remaniement « simplifie » la correction sans savoir ce
qu'elle protégeait.
"""
import math
import random
import unittest

from stockscan import backtest as bt
from stockscan import phases as ph
from stockscan import scoring as sc
from stockscan import structure as st

from tests import synth


def _walk(n, sigma, seed):
    rng = random.Random(seed)
    price = 100.0
    out = [price]
    for _ in range(n - 1):
        price *= math.exp(rng.gauss(0.0, sigma))
        out.append(price)
    return out


class TestCompressionWindows(unittest.TestCase):
    """Défaut 1 — la contraction se mesurait sur 15 séances contre 45.

    Racine de 15/45 vaut 0,577 : à volatilité rigoureusement constante, le
    rapport valait déjà 0,60 et 70 % du marché touchait la prime de
    contraction. Le signal mesurait la longueur des fenêtres, pas le marché.
    """

    def test_a_steady_ramp_shows_no_contraction(self):
        closes = [50.0 * (1.004 ** i) for i in range(300)]
        comp = st.compression(synth.build(closes))
        self.assertGreater(comp.range_contraction, 0.85, comp.notes)
        self.assertFalse(comp.detected)

    def test_constant_volatility_rarely_earns_the_credit(self):
        credited = 0
        trials = 60
        for seed in range(trials):
            comp = st.compression(synth.build(_walk(300, 0.012, seed)))
            if comp.range_contraction <= 0.75:
                credited += 1
        self.assertLess(credited / trials, 0.35,
                        f"{credited}/{trials} marches aléatoires à volatilité "
                        "constante décrites comme en contraction")

    def test_a_genuine_tightening_is_still_detected(self):
        comp = st.compression(synth.build(synth.tightening_base()))
        self.assertLess(comp.ratio, 1.0, comp.notes)


class TestTrendSymmetry(unittest.TestCase):
    """Défaut 3 — sans MM200, HAUSSIERE était inatteignable, BAISSIERE non."""

    def test_a_short_history_uptrend_can_be_bullish(self):
        t = st.trend(synth.build(synth.uptrend(150, daily=0.0018)))
        self.assertFalse(t.above_ma200)
        self.assertEqual(t.direction, st.HAUSSIERE, t.notes)

    def test_a_short_history_downtrend_is_still_bearish(self):
        t = st.trend(synth.build(synth.downtrend(150)))
        self.assertEqual(t.direction, st.BAISSIERE)

    def test_the_two_directions_use_the_same_evidence(self):
        up = st.trend(synth.build(synth.uptrend(150, daily=0.0018)))
        down = st.trend(synth.build(synth.downtrend(150)))
        self.assertGreater(up.score, down.score)


class TestNearestResistance(unittest.TestCase):
    """Défaut 4 — on prenait la résistance la mieux notée, pas la plus proche.

    Pour juger d'une pré-cassure, l'obstacle qui compte est le premier, pas le
    plus prestigieux.
    """

    def test_the_first_obstacle_wins(self):
        levels = [
            st.Resistance(level=140.0, tests=4, bars_since_last_test=200,
                          source="plus haut 52 sem.", quality=9.0),
            st.Resistance(level=104.0, tests=2, bars_since_last_test=8,
                          source="sommet local", quality=4.0),
        ]
        self.assertEqual(st.nearest_above(levels, 100.0).level, 104.0)

    def test_levels_below_the_price_are_ignored(self):
        levels = [st.Resistance(level=90.0, tests=3, bars_since_last_test=5,
                                source="sommet local", quality=8.0)]
        self.assertIsNone(st.nearest_above(levels, 100.0))
        self.assertIsNone(st.nearest_above([], 100.0))

    def test_quality_only_separates_two_neighbours(self):
        levels = [
            st.Resistance(level=104.0, tests=1, bars_since_last_test=5,
                          source="sommet local", quality=2.0),
            st.Resistance(level=104.3, tests=5, bars_since_last_test=5,
                          source="sommet local", quality=9.0),
        ]
        self.assertEqual(st.nearest_above(levels, 100.0).quality, 9.0)


class TestObvIsHistoryIndependent(unittest.TestCase):
    """Défaut 5 — l'OBV est cumulé : un % sur son niveau dépendait du nombre
    de séances chargées, pas du titre."""

    def test_the_same_sessions_give_the_same_reading(self):
        closes = synth.uptrend(300)
        full = synth.build(closes, synth.volumes_for(closes))
        cut = full.tail(120)
        self.assertAlmostEqual(st.accumulation(full).obv_change_volumes,
                               st.accumulation(cut).obv_change_volumes, places=2)

    def test_the_unit_is_days_of_average_volume(self):
        closes = [100.0 + i for i in range(200)]          # que des hausses
        bars = synth.build(closes, [1_000_000.0] * 200)
        acc = st.accumulation(bars, lookback=60)
        self.assertAlmostEqual(acc.obv_change_volumes, 60.0, places=1)


class TestNoFreePoints(unittest.TestCase):
    """Défaut 6 — un dossier entièrement vide marquait 7 points."""

    def test_an_empty_candidate_scores_zero(self):
        value, _why = sc.prebreakout_score(
            base=st.Base(), volume=st.VolumeProfile(), accum=st.Accumulation(),
            comp=st.Compression(), resistance=None, price=100.0,
            ext=st.Extension())
        self.assertEqual(value, 0.0)

    def test_a_measured_quiet_volume_still_scores(self):
        quiet = st.VolumeProfile(ratio20=0.8)
        value, _why = sc.prebreakout_score(
            base=st.Base(), volume=quiet, accum=st.Accumulation(),
            comp=st.Compression(), resistance=None, price=100.0,
            ext=st.Extension())
        self.assertGreater(value, 0.0)


class TestStructuralRewardToRisk(unittest.TestCase):
    """Défaut 2 — T1 valait entrée + 2R, donc rr valait 2,00 pour tout le
    monde et le filtre min_rr ne pouvait rien filtrer."""

    def _plan(self, closes, **kw):
        bars = synth.build(closes)
        base = st.detect_base(bars)
        res = st.nearest_above(st.find_resistances(bars), bars.close[-1])
        phase = ph.classify(bars, base=base, resistance=res,
                            comp=st.compression(bars),
                            volume=st.volume_profile(bars, base),
                            accum=st.accumulation(bars, res),
                            ext=st.extension(bars), trend=st.trend(bars))
        return ph.risk_plan(bars, phase=phase, base=base, resistance=res, **kw)

    def test_two_structures_give_two_different_ratios(self):
        thin = self._plan(synth.rally_then_flat_base(base=70, tight=0.004))
        deep = self._plan(synth.tightening_base())
        self.assertNotAlmostEqual(thin.rr, deep.rr, places=1)

    def test_the_gate_can_actually_reject(self):
        loose = self._plan(synth.tightening_base(), min_rr=2.0)
        strict = self._plan(synth.tightening_base(), min_rr=99.0)
        self.assertTrue(loose.tradeable)
        self.assertFalse(strict.tradeable)


class TestBacktestSplit(unittest.TestCase):
    """Défaut 7 — le découpage portait sur l'historique brut, amorçage et
    queue non testable compris."""

    def test_the_boundaries_follow_the_testable_range(self):
        first, last = 260, 940
        self.assertEqual(bt._period_of(first, first, last), bt.TRAIN)
        self.assertEqual(bt._period_of((first + last) // 2, first, last),
                         bt.VALIDATION)
        self.assertEqual(bt._period_of(last - 1, first, last), bt.OUT_OF_SAMPLE)

    def test_a_degenerate_range_does_not_divide_by_zero(self):
        self.assertEqual(bt._period_of(10, 10, 10), bt.TRAIN)


if __name__ == "__main__":
    unittest.main()
