"""Fondamentaux EDGAR — parsing XBRL et notation, sans réseau."""
import unittest

from stockscan import fundamentals as fu


def _q(start, end, val, filed, form="10-Q"):
    return {"start": start, "end": end, "val": val, "filed": filed, "form": form}


def _facts(**tags):
    return {"facts": {"us-gaap": {name: {"units": units}
                                  for name, units in tags.items()}}}


QUARTERS = [
    ("2024-01-01", "2024-03-31"), ("2024-04-01", "2024-06-30"),
    ("2024-07-01", "2024-09-30"), ("2024-10-01", "2024-12-31"),
    ("2025-01-01", "2025-03-31"), ("2025-04-01", "2025-06-30"),
    ("2025-07-01", "2025-09-30"), ("2025-10-01", "2025-12-31"),
]


def revenue_facts(values, tag="Revenues"):
    return {tag: {"USD": [_q(s, e, v, "2026-02-01")
                          for (s, e), v in zip(QUARTERS, values)]}}


class TestParsing(unittest.TestCase):
    def test_latest_filing_wins_for_a_duplicated_period(self):
        facts = _facts(Revenues={"USD": [
            _q("2025-01-01", "2025-03-31", 100.0, "2025-04-20"),
            _q("2025-01-01", "2025-03-31", 111.0, "2025-08-01", form="10-K"),
        ]})
        self.assertEqual(fu.durations(facts, ("Revenues",)), [("2025-03-31", 111.0)])

    def test_annual_spans_are_not_mistaken_for_quarters(self):
        facts = _facts(Revenues={"USD": [
            _q("2025-01-01", "2025-03-31", 100.0, "2025-04-20"),
            _q("2025-01-01", "2025-12-31", 430.0, "2026-02-01", form="10-K"),
        ]})
        quarters = fu.durations(facts, ("Revenues",))
        self.assertEqual(quarters, [("2025-03-31", 100.0)])
        years = fu.durations(facts, ("Revenues",), span=fu.YEAR)
        self.assertEqual(years, [("2025-12-31", 430.0)])

    def test_instants_ignore_durations(self):
        facts = _facts(StockholdersEquity={"USD": [
            {"end": "2025-03-31", "val": 500.0, "filed": "2025-04-20"},
            _q("2025-01-01", "2025-03-31", 999.0, "2025-04-20"),
        ]})
        self.assertEqual(fu.instants(facts, ("StockholdersEquity",)),
                         [("2025-03-31", 500.0)])

    def test_series_are_ordered_by_period_end(self):
        facts = _facts(**revenue_facts([1, 2, 3, 4, 5, 6, 7, 8]))
        ends = [d for d, _v in fu.durations(facts, ("Revenues",))]
        self.assertEqual(ends, sorted(ends))


class TestTtmAndGrowth(unittest.TestCase):
    def test_ttm_sums_the_last_four_quarters(self):
        facts = _facts(**revenue_facts([10, 10, 10, 10, 20, 20, 20, 20]))
        series = fu.durations(facts, ("Revenues",))
        self.assertEqual(fu.ttm(series), 80.0)
        self.assertEqual(fu.ttm(series, offset=4), 40.0)

    def test_growth_compares_ttm_to_ttm(self):
        facts = _facts(**revenue_facts([10, 10, 10, 10, 20, 20, 20, 20]))
        self.assertAlmostEqual(fu.growth_pct(fu.durations(facts, ("Revenues",))), 100.0)

    def test_growth_needs_eight_quarters(self):
        facts = _facts(**revenue_facts([10, 10, 10, 10]))
        series = fu.durations(facts, ("Revenues",))
        self.assertIsNone(fu.growth_pct(series))
        self.assertEqual(fu.ttm(series), 40.0)

    def test_growth_from_a_loss_is_not_a_percentage(self):
        facts = _facts(**revenue_facts([-5, -5, -5, -5, 20, 20, 20, 20]))
        self.assertIsNone(fu.growth_pct(fu.durations(facts, ("Revenues",))))


class TestAnalyse(unittest.TestCase):
    def _company(self, rev, net, eps, equity=1000.0, liab=500.0, gross=None, oper=None):
        tags = dict(revenue_facts(rev))
        tags["NetIncomeLoss"] = {"USD": [_q(s, e, v, "2026-02-01")
                                        for (s, e), v in zip(QUARTERS, net)]}
        tags["EarningsPerShareDiluted"] = {"USD/shares": [
            _q(s, e, v, "2026-02-01") for (s, e), v in zip(QUARTERS, eps)]}
        if gross:
            tags["GrossProfit"] = {"USD": [_q(s, e, v, "2026-02-01")
                                           for (s, e), v in zip(QUARTERS, gross)]}
        if oper:
            tags["OperatingIncomeLoss"] = {"USD": [_q(s, e, v, "2026-02-01")
                                                   for (s, e), v in zip(QUARTERS, oper)]}
        tags["StockholdersEquity"] = {"USD": [{"end": "2025-12-31", "val": equity,
                                               "filed": "2026-02-01"}]}
        tags["Liabilities"] = {"USD": [{"end": "2025-12-31", "val": liab,
                                        "filed": "2026-02-01"}]}
        return _facts(**tags)

    def test_no_data_is_unavailable_not_average(self):
        f = fu.analyse(None)
        self.assertFalse(f.available)
        self.assertIsNone(f.score)
        self.assertTrue(f.notes)

    def test_empty_facts_is_unavailable(self):
        f = fu.analyse(_facts())
        self.assertFalse(f.available)
        self.assertIsNone(f.score)

    def test_a_growing_profitable_company_scores_above_a_shrinking_one(self):
        good = fu.analyse(self._company(
            [100] * 4 + [140] * 4, [10] * 4 + [20] * 4, [1.0] * 4 + [2.0] * 4,
            gross=[60] * 4 + [84] * 4, oper=[25] * 4 + [40] * 4))
        bad = fu.analyse(self._company(
            [140] * 4 + [100] * 4, [20] * 4 + [2] * 4, [2.0] * 4 + [0.2] * 4,
            gross=[84] * 4 + [30] * 4, oper=[40] * 4 + [2] * 4))
        self.assertTrue(good.available and bad.available)
        self.assertGreater(good.score, bad.score)
        self.assertGreater(good.score, 5.0)
        self.assertLess(bad.score, 5.0)

    def test_margins_and_roe_are_computed_from_ttm(self):
        f = fu.analyse(self._company([100] * 8, [10] * 8, [1.0] * 8,
                                     gross=[60] * 8, oper=[25] * 8, equity=1000.0))
        self.assertAlmostEqual(f.gross_margin_pct, 60.0)
        self.assertAlmostEqual(f.operating_margin_pct, 25.0)
        self.assertAlmostEqual(f.roe_pct, 4.0)
        self.assertAlmostEqual(f.debt_to_equity, 0.5)

    def test_score_stays_inside_its_bounds(self):
        wild = fu.analyse(self._company([1] * 4 + [500] * 4, [1] * 4 + [400] * 4,
                                        [0.01] * 4 + [9.0] * 4,
                                        gross=[1] * 4 + [480] * 4,
                                        oper=[1] * 4 + [450] * 4, equity=10.0))
        self.assertLessEqual(wild.score, 10.0)
        self.assertGreaterEqual(wild.score, 0.0)


class TestClientIsOffline(unittest.TestCase):
    def test_client_construction_makes_no_request(self):
        client = fu.SecClient()
        self.assertEqual(client.stats["calls"], 0)
        self.assertIn("stockscan", client.user_agent)


if __name__ == "__main__":
    unittest.main()
