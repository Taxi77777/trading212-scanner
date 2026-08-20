"""Mécanique du backtest et statistiques — pas la rentabilité.

Aucun test ici ne prétend que la stratégie gagne : les séries sont synthétiques
et cassent par construction. On vérifie que la machine compte juste et ne
regarde pas l'avenir.
"""
import unittest

from stockscan import backtest as bt
from stockscan import stats

from tests import synth


def _long_series(cycles=6):
    closes = []
    for c in range(cycles):
        closes += synth.rally_then_flat_base(rally=90, base=40,
                                             start=40 * (1.15 ** c),
                                             top=60 * (1.15 ** c))
        closes += [closes[-1] * (1.01 + 0.004 * i) for i in range(20)]
    return closes


class TestStats(unittest.TestCase):
    def test_expectancy_and_profit_factor(self):
        p = stats.performance([2.0, -1.0, 2.0, -1.0])
        self.assertEqual(p.trades, 4)
        self.assertAlmostEqual(p.expectancy, 0.5)
        self.assertAlmostEqual(p.profit_factor, 2.0)
        self.assertAlmostEqual(p.win_rate, 50.0)

    def test_confidence_interval_widens_with_noise(self):
        calm = stats.performance([0.5] * 50)
        noisy = stats.performance([5.0, -4.0] * 25)
        self.assertLess(calm.ci95[1] - calm.ci95[0], noisy.ci95[1] - noisy.ci95[0])

    def test_small_sample_is_never_declared_positive(self):
        p = stats.performance([2.0] * 10)
        self.assertEqual(p.verdict, "ÉCHANTILLON INSUFFISANT")

    def test_an_interval_straddling_zero_is_indistinguishable(self):
        # 50 % de reussite a 1:1 : rigoureusement rien. L'intervalle doit le dire.
        p = stats.performance([1.0, -1.0] * 20)
        self.assertGreaterEqual(p.trades, 30)
        self.assertFalse(p.significant)
        self.assertEqual(p.verdict, "INDISCERNABLE DE ZÉRO")

    def test_breakeven_win_rate(self):
        self.assertAlmostEqual(stats.breakeven_win_rate(1.0), 50.0)
        self.assertAlmostEqual(stats.breakeven_win_rate(2.0), 100 / 3, places=4)
        self.assertEqual(stats.breakeven_win_rate(0.0), 0.0)

    def test_max_drawdown_is_negative_or_zero(self):
        self.assertAlmostEqual(stats.max_drawdown([1, 1, 1]), 0.0)
        self.assertAlmostEqual(stats.max_drawdown([1, -3, 1]), -3.0)

    def test_empty_series_is_empty_not_zero_percent(self):
        p = stats.performance([])
        self.assertEqual(p.trades, 0)
        self.assertEqual(p.win_rate, 0.0)


class TestSimulate(unittest.TestCase):
    def _bars(self, closes):
        return synth.build(closes)

    def test_target_reached_pays_the_planned_multiple(self):
        bars = self._bars([100.0] * 10 + [101.0, 104.0, 108.0] + [108.0] * 10)
        outcome, _i, r = bt.simulate(bars, 9, entry=101.0, stop=99.0, target=105.0)
        self.assertEqual(outcome, "GAGNE")
        self.assertGreater(r, 1.8)
        self.assertLess(r, 2.0)          # les frais mordent un peu

    def test_stop_hit_costs_about_one_r(self):
        bars = self._bars([100.0] * 10 + [101.5, 97.0, 96.0] + [96.0] * 10)
        outcome, _i, r = bt.simulate(bars, 9, entry=101.0, stop=99.0, target=105.0)
        self.assertEqual(outcome, "PERDU")
        self.assertLess(r, -1.0)
        self.assertGreater(r, -1.2)

    def test_an_ambiguous_bar_is_counted_as_a_loss(self):
        """Une bougie qui touche stop ET objectif : on compte la perte."""
        bars = self._bars([100.0] * 10 + [101.0, 103.0] + [103.0] * 5)
        bars.high[11] = 120.0
        bars.low[11] = 90.0
        outcome, _i, r = bt.simulate(bars, 9, entry=101.0, stop=99.0, target=105.0)
        self.assertEqual(outcome, "PERDU")
        self.assertLess(r, 0)

    def test_a_plan_never_triggered_is_not_a_trade(self):
        bars = self._bars([100.0] * 30)
        outcome, _i, r = bt.simulate(bars, 5, entry=150.0, stop=140.0, target=170.0)
        self.assertEqual(outcome, "NON_DECLENCHE")
        self.assertEqual(r, 0.0)

    def test_an_impossible_plan_is_refused(self):
        bars = self._bars([100.0] * 30)
        outcome, _i, _r = bt.simulate(bars, 5, entry=100.0, stop=100.0, target=110.0)
        self.assertEqual(outcome, "NON_DECLENCHE")

    def test_time_exit_when_nothing_is_touched(self):
        bars = self._bars([100.0] * 10 + [101.0] * 40)
        outcome, _i, _r = bt.simulate(bars, 9, entry=101.0, stop=90.0, target=130.0,
                                      max_bars=20)
        self.assertEqual(outcome, "TEMPS")


class TestNoLookAhead(unittest.TestCase):
    def test_truncating_the_future_does_not_change_past_trades(self):
        closes = _long_series()
        full = synth.build(closes)
        bench = synth.build(synth.uptrend(len(closes), daily=0.0004))
        cut = int(len(closes) * 0.7)
        short = full.head(cut)
        short_bench = bench.head(cut)

        a = bt.walk(full, bench, step=5, min_score=40.0)
        b = bt.walk(short, short_bench, step=5, min_score=40.0)
        horizon = cut - 60          # au-delà, le résultat dépend de séances absentes
        key = lambda t: (t.entry_index, round(t.entry, 4), round(t.stop, 4),
                         round(t.r_multiple, 4), t.phase)
        self.assertEqual([key(t) for t in a if t.entry_index < horizon],
                         [key(t) for t in b if t.entry_index < horizon])

    def test_periods_split_the_testable_range_not_the_raw_history(self):
        # Plage testable [260, 940[ : le decoupage doit porter sur ELLE.
        first, last = 260, 940
        self.assertEqual(bt._period_of(300, first, last), bt.TRAIN)
        self.assertEqual(bt._period_of(650, first, last), bt.VALIDATION)
        self.assertEqual(bt._period_of(900, first, last), bt.OUT_OF_SAMPLE)
        # Sous l'ancienne regle (ratio sur 0..total) la seance 300 d'un
        # historique de 1000 tombait aussi en apprentissage, mais la 650
        # basculait en validation seulement par coincidence : on verifie que
        # les bornes suivent bien la plage testable, pas l'historique brut.
        self.assertEqual(bt._period_of(first, first, last), bt.TRAIN)
        self.assertEqual(bt._period_of(last - 1, first, last), bt.OUT_OF_SAMPLE)

    def test_the_split_is_balanced_over_the_testable_range(self):
        first, last, step = 260, 940, 5
        counts = {}
        for i in range(first, last, step):
            name = bt._period_of(i, first, last)
            counts[name] = counts.get(name, 0) + 1
        total = sum(counts.values())
        self.assertAlmostEqual(counts[bt.TRAIN] / total, 0.50, delta=0.02)
        self.assertAlmostEqual(counts[bt.VALIDATION] / total, 0.25, delta=0.02)
        self.assertAlmostEqual(counts[bt.OUT_OF_SAMPLE] / total, 0.25, delta=0.02)


class TestAggregate(unittest.TestCase):
    def test_phases_are_measured_separately(self):
        trades = [bt.Trade(phase="PRE_BREAKOUT", r_multiple=2.0, period=bt.TRAIN),
                  bt.Trade(phase="PRE_BREAKOUT", r_multiple=-1.0, period=bt.TRAIN),
                  bt.Trade(phase="BREAKOUT", r_multiple=-1.0, period=bt.OUT_OF_SAMPLE)]
        result = bt.aggregate(trades)
        self.assertEqual(set(result.by_phase), {"PRE_BREAKOUT", "BREAKOUT"})
        self.assertAlmostEqual(result.by_phase["PRE_BREAKOUT"].expectancy, 0.5)
        self.assertAlmostEqual(result.by_phase["BREAKOUT"].expectancy, -1.0)
        self.assertAlmostEqual(result.overall.expectancy, 0.0)

    def test_overfitting_is_flagged_when_the_edge_collapses(self):
        trades = [bt.Trade(phase="X", r_multiple=2.0, period=bt.TRAIN) for _ in range(25)]
        trades += [bt.Trade(phase="X", r_multiple=-1.0, period=bt.OUT_OF_SAMPLE)
                   for _ in range(25)]
        result = bt.aggregate(trades)
        self.assertTrue(any("sur-ajustement" in n for n in result.notes), result.notes)

    def test_a_thin_split_says_so_instead_of_concluding(self):
        result = bt.aggregate([bt.Trade(phase="X", r_multiple=1.0, period=bt.TRAIN)])
        self.assertTrue(any("trop peu" in n for n in result.notes))

    def test_report_is_never_empty(self):
        self.assertTrue(bt.aggregate([]).report())


if __name__ == "__main__":
    unittest.main()


class TestChronologicalOrder(unittest.TestCase):
    """Le drawdown se lit sur une courbe de capital, donc dans l'ordre du temps.

    Les trades sont produits valeur par valeur : sans tri, la « pire série »
    décrivait un compte où l'on aurait fini de trader la première action avant
    d'ouvrir la seconde. Le chiffre publié était trois fois trop grand.
    """

    def _alternes(self):
        gagnants = [bt.Trade(ticker="A", phase="X", r_multiple=2.0, entry_ts=t,
                             period=bt.TRAIN) for t in (10, 30, 50)]
        perdants = [bt.Trade(ticker="B", phase="X", r_multiple=-1.0, entry_ts=t,
                             period=bt.TRAIN) for t in (20, 40, 60)]
        return gagnants + perdants

    def test_trades_are_reordered_by_date(self):
        result = bt.aggregate(self._alternes())
        self.assertEqual([t.entry_ts for t in result.trades], [10, 20, 30, 40, 50, 60])

    def test_the_drawdown_uses_the_real_sequence(self):
        brut = stats.max_drawdown([t.r_multiple for t in self._alternes()])
        result = bt.aggregate(self._alternes())
        self.assertLess(abs(result.overall.max_drawdown_r), abs(brut))
        self.assertAlmostEqual(result.overall.max_drawdown_r, -1.0)

    def test_order_independent_statistics_are_unchanged(self):
        """L'espérance et le profit factor ne dépendent pas de l'ordre : le tri
        ne doit rien changer pour eux."""
        result = bt.aggregate(self._alternes())
        self.assertAlmostEqual(result.overall.expectancy, 0.5)
        self.assertAlmostEqual(result.overall.profit_factor, 2.0)

    def test_walk_stamps_real_timestamps(self):
        closes = _long_series()
        vols = synth.volumes_for(closes, dry_from=int(len(closes) * 0.75))
        bars = synth.build(closes, vols)
        bench = synth.build(synth.uptrend(len(closes), daily=0.0004))
        trades = bt.walk(bars, bench, step=5, min_score=40.0)
        if not trades:
            self.skipTest("aucun trade sur cette serie")
        for t in trades:
            self.assertEqual(t.entry_ts, bars.ts[t.entry_index])
            self.assertGreaterEqual(t.exit_ts, t.entry_ts)
