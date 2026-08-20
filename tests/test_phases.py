"""Phases du cycle et plan de risque."""
import unittest

from stockscan import market_data as md
from stockscan import phases as ph
from stockscan import structure as st
from stockscan import strength as sg

from tests import synth


def _inputs(closes, volumes=None):
    bars = synth.build(closes, volumes)
    base = st.detect_base(bars)
    res = st.find_resistances(bars)
    top = res[0] if res else None
    return bars, dict(
        base=base,
        resistance=top,
        comp=st.compression(bars),
        volume=st.volume_profile(bars, base),
        accum=st.accumulation(bars, top),
        ext=st.extension(bars),
        trend=st.trend(bars),
    )


class TestClassify(unittest.TestCase):
    def test_flat_base_under_resistance_is_prebreakout_or_early(self):
        bars, kw = _inputs(synth.tightening_base())
        phase = ph.classify(bars, **kw)
        self.assertIn(phase.name, (ph.PRE_BREAKOUT, ph.EARLY), phase.reasons)
        self.assertTrue(phase.reasons)

    def test_fresh_break_of_the_pivot_is_a_breakout(self):
        closes = synth.base_then_breakout(after=2)
        vols = synth.volumes_for(closes, dry_from=200, surge_last=2)
        bars, kw = _inputs(closes, vols)
        phase = ph.classify(bars, **kw)
        self.assertEqual(phase.name, ph.BREAKOUT, phase.reasons)
        self.assertLessEqual(phase.bars_since_breakout, 2)

    def test_return_to_the_old_resistance_is_a_retest(self):
        bars, kw = _inputs(synth.breakout_then_retest())
        phase = ph.classify(bars, **kw)
        self.assertEqual(phase.name, ph.RETEST, phase.reasons)
        self.assertLess(abs(phase.distance_to_pivot_pct), 5.0)

    def test_a_stock_that_already_ran_is_acceleration_not_prebreakout(self):
        closes = synth.uptrend(260, daily=0.0018)
        closes += [closes[-1] * (1 + 0.035) ** (i + 1) for i in range(12)]
        bars, kw = _inputs(closes)
        phase = ph.classify(bars, **kw)
        self.assertEqual(phase.name, ph.ACCELERATION, phase.reasons)

    def test_a_downtrend_in_a_range_is_never_a_prebreakout(self):
        """Un cours colle sous une resistance dans une tendance cassee n'est pas
        une pre-cassure : c'est un rebond technique dans une baisse."""
        closes = synth.downtrend(400, daily=-0.0012)
        closes += [closes[-1] * (1 + 0.0004 * (i % 5 - 2)) for i in range(60)]
        bars, kw = _inputs(closes)
        phase = ph.classify(bars, **kw)
        self.assertNotEqual(phase.name, ph.PRE_BREAKOUT, phase.reasons)

    def test_downtrend_is_no_trade(self):
        bars, kw = _inputs(synth.downtrend(300))
        phase = ph.classify(bars, **kw)
        self.assertEqual(phase.name, ph.NO_TRADE, phase.reasons)

    def test_short_history_is_no_trade_not_a_guess(self):
        bars, kw = _inputs(synth.uptrend(30))
        phase = ph.classify(bars, **kw)
        self.assertEqual(phase.name, ph.NO_TRADE)

    def test_every_phase_has_a_label_and_an_emoji(self):
        for name in (ph.EARLY, ph.PRE_BREAKOUT, ph.BREAKOUT,
                     ph.RETEST, ph.ACCELERATION, ph.NO_TRADE):
            self.assertIn(name, ph.PHASE_LABELS)
            self.assertIn(name, ph.PHASE_EMOJI)


REALISTIC = None            # rempli a l'import : une base aux proportions reelles


class TestRiskPlan(unittest.TestCase):
    """Les plans se testent sur une base realiste (~12 % de profondeur).

    Une base de 1 % donne un objectif de 1 % : le R:R y vaut mecaniquement
    moins de 1 quel que soit le moteur. Tester la dessus mesurerait la
    geometrie du triangle, pas le code.
    """

    def _plan(self, closes, **kw):
        bars, pieces = _inputs(closes)
        phase = ph.classify(bars, **pieces)
        return phase, ph.risk_plan(bars, phase=phase, base=pieces["base"],
                                   resistance=pieces["resistance"], **kw)

    def test_no_trade_produces_no_plan(self):
        _, plan = self._plan(synth.downtrend(300))
        self.assertFalse(plan.tradeable)
        self.assertEqual(plan.entry, 0.0)
        self.assertTrue(plan.notes)

    def test_acceleration_produces_no_plan(self):
        closes = synth.uptrend(260, daily=0.0018)
        closes += [closes[-1] * (1 + 0.035) ** (i + 1) for i in range(12)]
        _, plan = self._plan(closes)
        self.assertFalse(plan.tradeable)

    def test_stop_is_always_below_the_entry(self):
        phase, plan = self._plan(synth.tightening_base())
        self.assertIn(phase.name, (ph.PRE_BREAKOUT, ph.EARLY))
        self.assertGreater(plan.entry, 0.0)
        self.assertLess(plan.stop, plan.entry)
        self.assertGreater(plan.risk_pct, 0.0)

    def test_targets_are_expressed_in_r_and_ordered(self):
        _, plan = self._plan(synth.tightening_base())
        self.assertTrue(plan.targets)
        rs = [t.r_multiple for t in plan.targets]
        self.assertEqual(rs, sorted(rs))
        self.assertEqual(plan.rr, rs[0])

    def test_rr_below_the_minimum_is_refused(self):
        _, plan = self._plan(synth.tightening_base(),
                             min_rr=99.0)
        self.assertFalse(plan.tradeable)
        self.assertTrue(any("R:R" in n for n in plan.notes), plan.notes)

    def test_risk_off_regime_blocks_new_positions(self):
        bars, pieces = _inputs(synth.tightening_base())
        phase = ph.classify(bars, **pieces)
        blocked = sg.MarketRegime(label=sg.RISK_OFF, score=10.0,
                                  allow_new_positions=False, size_multiplier=0.4)
        plan = ph.risk_plan(bars, phase=phase, base=pieces["base"],
                            resistance=pieces["resistance"], regime=blocked)
        self.assertFalse(plan.tradeable)

    def test_weaker_regime_reduces_the_size(self):
        bars, pieces = _inputs(synth.tightening_base())
        phase = ph.classify(bars, **pieces)
        # Risque volontairement faible : sans cela le plafond de taille lie les
        # deux plans a 20 % et le test ne mesurerait plus le regime.
        common = dict(base=pieces["base"], resistance=pieces["resistance"],
                      account_risk_pct=0.05)
        full = ph.risk_plan(bars, phase=phase, **common,
                            regime=sg.MarketRegime(label=sg.RISK_ON, size_multiplier=1.0))
        half = ph.risk_plan(bars, phase=phase, **common,
                            regime=sg.MarketRegime(label=sg.NEUTRE, size_multiplier=0.7))
        if not full.tradeable:
            self.skipTest("plan rejeté en amont")
        self.assertFalse(full.size_capped)
        self.assertGreater(full.position_pct, half.position_pct)

    def test_position_size_is_capped(self):
        _, plan = self._plan(synth.tightening_base())
        if not plan.tradeable:
            self.skipTest("plan rejeté en amont")
        self.assertLessEqual(plan.position_pct, ph.MAX_POSITION_PCT)
        if plan.size_capped:
            self.assertTrue(any("plafonn" in n for n in plan.notes))

    def test_target_ladder_is_strictly_increasing(self):
        _, plan = self._plan(synth.tightening_base())
        prices = [t.price for t in plan.targets]
        multiples = [t.r_multiple for t in plan.targets]
        self.assertEqual(prices, sorted(prices))
        self.assertEqual(multiples, sorted(multiples))
        self.assertEqual(len(set(prices)), 3)

    def test_reward_to_risk_comes_from_the_chart_not_from_arithmetic(self):
        """Le defaut corrige : T1 = entree + 2R rendait rr constant a 2,00.

        Deux structures differentes doivent produire deux R:R differents, sinon
        le filtre min_rr ne filtre rien.
        """
        _, thin = self._plan(synth.rally_then_flat_base(base=70, tight=0.004))
        _, deep = self._plan(synth.tightening_base())
        self.assertNotAlmostEqual(thin.rr, deep.rr, places=1)
        self.assertLess(thin.rr, 2.0)
        self.assertGreater(deep.rr, 2.0)
        self.assertFalse(thin.tradeable)
        self.assertTrue(deep.tradeable)

    def test_a_paper_thin_base_cannot_pay_for_its_own_risk(self):
        _, plan = self._plan(synth.rally_then_flat_base(base=70, tight=0.004))
        self.assertFalse(plan.tradeable)
        self.assertTrue(any("R:R structurel" in n for n in plan.notes), plan.notes)

    def test_stop_is_never_tighter_than_the_daily_noise(self):
        bars, pieces = _inputs(synth.tightening_base())
        phase = ph.classify(bars, **pieces)
        plan = ph.risk_plan(bars, phase=phase, base=pieces["base"],
                            resistance=pieces["resistance"])
        atr = md.atr(bars, 14) or 0.0
        self.assertLessEqual(plan.stop, plan.entry - 1.5 * atr + 1e-6)

    def test_without_a_base_there_is_no_measurable_objective(self):
        bars, pieces = _inputs(synth.tightening_base())
        phase = ph.classify(bars, **pieces)
        plan = ph.risk_plan(bars, phase=phase, base=st.Base(),
                            resistance=pieces["resistance"])
        self.assertFalse(plan.tradeable)
        self.assertTrue(any("mesurable" in n for n in plan.notes), plan.notes)

    def test_plan_states_that_nothing_is_executed(self):
        _, plan = self._plan(synth.tightening_base())
        if plan.tradeable:
            self.assertTrue(any("aucun ordre" in n.lower() for n in plan.notes))

    def test_none_never_raises(self):
        plan = ph.risk_plan(None, phase=ph.Phase(), base=st.Base(), resistance=None)
        self.assertFalse(plan.tradeable)


if __name__ == "__main__":
    unittest.main()
