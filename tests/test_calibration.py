"""Les quatre correctifs de calibrage, chacun avec sa garantie."""
import unittest

from stockscan import phases as ph
from stockscan import scoring as sc
from stockscan import strength as sg
from stockscan import structure as st

from tests import synth


class TestAbsoluteStrength(unittest.TestCase):
    """Correctif 1 — la force relative punissait les marchés vigoureux."""

    def test_the_same_stock_is_judged_the_same_whatever_the_index(self):
        """C'est tout l'objet : la force propre ne regarde aucun indice."""
        action = synth.build(synth.uptrend(300, daily=0.00065, wobble=0.004))
        scores = {sg.absolute_strength(action).score}
        # On la re-mesure : rien d'exterieur ne peut la faire varier.
        self.assertEqual(len(scores), 1)
        self.assertTrue(sg.absolute_strength(action).available)

    def test_the_benchmark_handicap_is_halved(self):
        """La moitié des points de force ne dépend plus de la place cotée."""
        action = synth.build(synth.uptrend(300, daily=0.00065, wobble=0.004))
        fort = synth.build(synth.uptrend(300, daily=0.00089, wobble=0.004))
        atone = synth.build(synth.uptrend(300, daily=0.00008, wobble=0.004))

        def points(indice):
            rs = sg.relative_strength(action, indice)
            ab = sg.absolute_strength(action)
            return (min(1.0, rs.score / 15.0) * sc.WEIGHTS["relative"]
                    + min(1.0, ab.score / 10.0) * sc.WEIGHTS["absolute"])

        ecart = points(atone) - points(fort)
        self.assertLess(ecart, 6.0, f"handicap encore de {ecart:.1f} points")

    def test_a_stock_far_below_its_high_is_not_strong(self):
        faible = synth.build(synth.downtrend(300))
        forte = synth.build(synth.uptrend(300, daily=0.0015))
        self.assertLess(sg.absolute_strength(faible).score,
                        sg.absolute_strength(forte).score)

    def test_short_history_is_unavailable_not_zero(self):
        court = sg.absolute_strength(synth.build(synth.uptrend(50)))
        self.assertFalse(court.available)
        self.assertTrue(court.notes)

    def test_a_flat_line_is_not_strong(self):
        """Etre a son plus haut annuel parce qu'on n'a jamais bouge n'est pas
        de la force. Sans cette garde, une cotation figee marquait 6 sur 10."""
        immobile = sg.absolute_strength(synth.build([50.0] * 300))
        montante = sg.absolute_strength(synth.build(synth.uptrend(300, daily=0.0012)))
        self.assertLessEqual(immobile.score, 2.5)
        self.assertGreater(montante.score - immobile.score, 5.0)

    def test_a_frozen_quote_is_reported_not_scored(self):
        fige = synth.build([50.0] * 300)
        fige.high = list(fige.close)
        fige.low = list(fige.close)
        fige.open = list(fige.close)
        result = sg.absolute_strength(fige)
        self.assertFalse(result.available)
        self.assertEqual(result.score, 0.0)
        self.assertTrue(any("figée" in n for n in result.notes))

    def test_none_never_raises(self):
        self.assertFalse(sg.absolute_strength(None).available)

    def test_score_is_bounded(self):
        for series in (synth.uptrend(300, daily=0.01), synth.downtrend(300, daily=-0.01)):
            value = sg.absolute_strength(synth.build(series)).score
            self.assertGreaterEqual(value, 0.0)
            self.assertLessEqual(value, 10.0)


class TestWeightsAfterSplit(unittest.TestCase):
    def test_the_total_is_still_one_hundred(self):
        self.assertAlmostEqual(sum(sc.WEIGHTS.values()), 100.0)

    def test_force_is_split_in_two_halves(self):
        self.assertEqual(sc.WEIGHTS["relative"], sc.WEIGHTS["absolute"])

    def test_every_weight_still_has_a_label(self):
        self.assertEqual(set(sc.WEIGHTS), set(sc.LABELS))

    def test_a_missing_absolute_strength_scores_zero_not_average(self):
        bars = synth.build(synth.tightening_base())
        base = st.detect_base(bars)
        res = st.nearest_above(st.find_resistances(bars), bars.close[-1])
        common = dict(trend=st.trend(bars), base=base,
                      volume=st.volume_profile(bars, base),
                      accum=st.accumulation(bars, res), comp=st.compression(bars),
                      rs=sg.RelativeStrength(), resistance=res,
                      ext=st.extension(bars), price=bars.close[-1])
        sans = sc.score(**common)
        self.assertEqual(sans.component("absolute").points, 0.0)
        self.assertTrue(sans.component("absolute").why)


class TestCorrelationCap(unittest.TestCase):
    """Correctif 4 — trois banques ne font pas trois signaux."""

    class Faux:
        def __init__(self, ticker):
            self.ticker = ticker

    def _series(self, mapping):
        return {k: synth.build(v) for k, v in mapping.items()}

    def test_a_third_twin_is_dropped(self):
        base = synth.uptrend(200, daily=0.001, wobble=0.006)
        series = self._series({t: list(base) for t in ("A", "B", "C")})
        gardes, ecartes = sg.limit_concentration(
            [self.Faux(t) for t in ("A", "B", "C")], series, max_per_cluster=2)
        self.assertEqual([c.ticker for c in gardes], ["A", "B"])
        self.assertEqual(len(ecartes), 1)
        self.assertEqual(ecartes[0][0].ticker, "C")

    def test_the_best_ranked_keeps_its_place(self):
        base = synth.uptrend(200, daily=0.001, wobble=0.006)
        series = self._series({t: list(base) for t in ("X", "Y")})
        gardes, _ = sg.limit_concentration(
            [self.Faux("X"), self.Faux("Y")], series, max_per_cluster=1)
        self.assertEqual([c.ticker for c in gardes], ["X"])

    def test_unrelated_stocks_are_all_kept(self):
        series = self._series({
            "A": synth.uptrend(200, daily=0.0012, wobble=0.004),
            "B": synth.downtrend(200, daily=-0.0009, wobble=0.011),
            "C": synth.tightening_base(rally=120, base=80),
        })
        gardes, ecartes = sg.limit_concentration(
            [self.Faux(t) for t in ("A", "B", "C")], series, max_corr=0.99)
        self.assertEqual(len(gardes), 3)
        self.assertEqual(ecartes, [])

    def test_nothing_is_dropped_silently(self):
        """Un ecart doit toujours dire avec QUI la valeur etait correlee."""
        base = synth.uptrend(200, daily=0.001, wobble=0.006)
        series = self._series({t: list(base) for t in ("A", "B", "C")})
        _, ecartes = sg.limit_concentration(
            [self.Faux(t) for t in ("A", "B", "C")], series, max_per_cluster=2)
        candidate, corr, avec = ecartes[0]
        self.assertGreaterEqual(corr, 0.75)
        self.assertEqual(avec.ticker, "A")

    def test_market_beta_is_removed_before_judging_kinship(self):
        """Deux titres qui ne partagent que le marche ne sont pas jumeaux.

        La correlation brute est dominee par le beta : sur 60 candidats d'un
        meme marche, le plafond en ecartait 58. En retirant le mouvement de
        l'indice il ne reste que la co-variation propre.
        """
        import math
        idx = [100.0]
        for i in range(1, 300):
            idx.append(idx[-1] * (1 + 0.0005 + 0.008 * math.sin(i / 11.0)))
        indice = synth.build(idx)

        def suit_le_marche(prix0, phase):
            out = [prix0]
            for i in range(1, 300):
                r = idx[i] / idx[i - 1] - 1
                out.append(out[-1] * (1 + r + 0.006 * math.sin(i / 3.7 + phase)))
            return synth.build(out)

        a, b = suit_le_marche(50, 0.0), suit_le_marche(70, 2.4)
        jumelle = suit_le_marche(50, 0.0)

        brute = sg.correlation(sg.returns(a), sg.returns(b))
        nette = sg.correlation(sg.excess_returns(a, indice),
                               sg.excess_returns(b, indice))
        self.assertLess(nette, 0.75, "le beta n'a pas ete retire")
        self.assertGreater(
            sg.correlation(sg.excess_returns(a, indice),
                           sg.excess_returns(jumelle, indice)), 0.9,
            "une vraie jumelle doit rester detectee")
        self.assertIsNotNone(brute)

    def test_without_a_benchmark_it_falls_back_to_raw_returns(self):
        bars = synth.build(synth.uptrend(200, daily=0.001))
        self.assertEqual(len(sg.excess_returns(bars, None)),
                         len(sg.returns(bars)))

    def test_missing_history_never_crashes(self):
        gardes, _ = sg.limit_concentration(
            [self.Faux("A"), self.Faux("B")], {}, max_per_cluster=1)
        self.assertEqual(len(gardes), 2)   # sans donnee, on ne regroupe pas

    def test_correlation_needs_enough_points(self):
        self.assertIsNone(sg.correlation([0.01] * 10, [0.01] * 10))


class TestPreBreakoutIsRare(unittest.TestCase):
    """Correctif 2 — 23 % du marche classe en pre-cassure ne veut rien dire."""

    def _classify(self, closes, volumes):
        bars = synth.build(closes, volumes)
        base = st.detect_base(bars)
        res = st.nearest_above(st.find_resistances(bars), bars.close[-1])
        return ph.classify(bars, base=base, resistance=res,
                           comp=st.compression(bars),
                           volume=st.volume_profile(bars, base),
                           accum=st.accumulation(bars, res),
                           ext=st.extension(bars), trend=st.trend(bars))

    def test_compression_alone_is_not_enough(self):
        closes = synth.tightening_base()
        phase = self._classify(closes, [1_000_000.0] * len(closes))
        self.assertNotEqual(phase.name, ph.PRE_BREAKOUT, phase.reasons)

    def test_volume_evidence_makes_the_difference(self):
        closes = synth.tightening_base()
        vols = synth.volumes_for(closes, dry_from=int(len(closes) * 0.75))
        self.assertIn(self._classify(closes, vols).name,
                      (ph.PRE_BREAKOUT, ph.EARLY))

    def test_early_is_reachable(self):
        """Correctif 3 — EARLY n'avait jamais ete atteinte sur 541 valeurs."""
        closes = synth.deep_tightening_base()
        vols = synth.volumes_for(closes, dry_from=int(len(closes) * 0.75))
        self.assertEqual(self._classify(closes, vols).name, ph.EARLY)


if __name__ == "__main__":
    unittest.main()


class TestOnePivotOnly(unittest.TestCase):
    """Le score et la phase doivent parler du MÊME niveau.

    Sinon le message annonce « résistance encore à 8,4 % » tout en accordant
    7,9 points de proximité sur 8 comme si elle était à 1,9 %. Un rapport qui
    se contredit lui-même ne vaut rien.
    """

    def _pieces(self, closes):
        vols = synth.volumes_for(closes, dry_from=int(len(closes) * 0.75))
        bars = synth.build(closes, vols)
        base = st.detect_base(bars)
        res = st.nearest_above(st.find_resistances(bars), bars.close[-1])
        return bars, base, res

    def test_the_scored_distance_matches_the_phase_distance(self):
        for closes in (synth.tightening_base(), synth.deep_tightening_base(),
                       synth.rally_then_flat_base(base=70, tight=0.004)):
            bars, base, res = self._pieces(closes)
            pivot = ph.pivot_level(base, res)
            phase = ph.classify(bars, base=base, resistance=res,
                                comp=st.compression(bars),
                                volume=st.volume_profile(bars, base),
                                accum=st.accumulation(bars, res),
                                ext=st.extension(bars), trend=st.trend(bars))
            if phase.pivot:
                self.assertAlmostEqual(phase.pivot, pivot, places=6)

    def test_proximity_uses_the_pivot_when_given(self):
        loin = st.Resistance(level=101.0, tests=3, bars_since_last_test=5,
                             source="swing", quality=8.0)
        proche, _ = sc.proximity_score(loin, 100.0)
        avec_pivot, _ = sc.proximity_score(loin, 100.0, pivot=140.0)
        self.assertGreater(proche, avec_pivot)

    def test_the_base_high_wins_over_a_minor_swing(self):
        base = st.Base(kind=st.RANGE, label="Range", length=40, high=95.0,
                       low=80.0, quality=8.0)
        mineure = st.Resistance(level=88.0, tests=2, bars_since_last_test=3,
                                source="swing", quality=5.0)
        self.assertEqual(ph.pivot_level(base, mineure), 95.0)

    def test_without_a_base_the_resistance_is_used(self):
        mineure = st.Resistance(level=88.0, tests=2, bars_since_last_test=3,
                                source="swing", quality=5.0)
        self.assertEqual(ph.pivot_level(st.Base(), mineure), 88.0)
        self.assertEqual(ph.pivot_level(st.Base(), None), 0.0)


class TestLisibilite(unittest.TestCase):
    """Ce que l'utilisateur lit doit être identifiable et exact."""

    def test_a_london_price_is_never_shown_as_pounds(self):
        """Londres cote en pence. « 1295 GBP » se lit mille livres au lieu de
        treize : un facteur cent sur un message d'aide à la décision."""
        from stockscan.telegram import _prix
        rendu = _prix(1295.0, "GBp")
        self.assertIn("pence", rendu)
        self.assertIn("12.95", rendu)
        self.assertNotIn("GBP", rendu)

    def test_other_currencies_are_left_alone(self):
        from stockscan.telegram import _prix
        self.assertEqual(_prix(53.05, "EUR"), "53.05 EUR")
        self.assertEqual(_prix(212.4, "USD"), "212.40 USD")

    def test_bars_carry_name_and_currency_through_slicing(self):
        from stockscan.market_data import Bars
        b = Bars([1, 2, 3], [1, 2, 3], [1, 2, 3], [1, 2, 3], [1, 2, 3], [1, 2, 3],
                 "Recordati SpA", "EUR")
        for vue in (b.tail(2), b.head(2)):
            self.assertEqual(vue.name, "Recordati SpA")
            self.assertEqual(vue.currency, "EUR")

    def test_bars_without_metadata_still_work(self):
        from stockscan.market_data import Bars
        b = Bars([1], [1], [1], [1], [1], [1])
        self.assertEqual(b.name, "")
        self.assertEqual(b.currency, "")

    def test_the_message_shows_the_company_not_only_the_ticker(self):
        from stockscan import telegram as tg
        from stockscan import phases as ph2

        class Faux:
            ticker, name, market_label, currency = "REC", "Recordati SpA", "Milan", "EUR"
            price = 53.05
            phase = ph2.Phase(name=ph2.RETEST, label="Retest")
            plan = ph2.RiskPlan()
            score = sc.Score()
            base = st.Base()
            resistance = None
            rs = sg.RelativeStrength()
            absolute = sg.AbsoluteStrength()
            trend = st.TrendState()
            ai: dict = {}

        rendu = tg.format_candidate(Faux())
        self.assertIn("Recordati SpA", rendu)
        self.assertIn("REC", rendu)

    def test_the_ai_is_asked_to_answer_in_french(self):
        from stockscan import ai_judge
        self.assertIn("FRENCH", ai_judge.SYSTEM_PROMPT)
