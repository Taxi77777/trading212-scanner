"""Surveillance intraséance : ce qui déclenche, et surtout ce qui se tait."""
import json
import os
import tempfile
import unittest

from stockscan import market_data as md
from stockscan import telegram as tg
from stockscan import watchlist as wl

from tests import synth

JOUR = 86400


def _plan(**kw):
    base = dict(ticker="REC", symbol="REC.MI", name="Recordati SpA",
                market_label="Milan", currency="EUR", phase="RETEST",
                entry=100.0, stop=95.0, target=115.0, created_ts=1000)
    base.update(kw)
    return wl.Watch(**base)


def _bars(closes, ts0=1000, step=900, hauts=None, bas=None):
    """Barres explicites : un test de seuil doit dire exactement ce qu'il teste.

    Les fixtures synthetiques calculent une meche proportionnelle au mouvement.
    Sur un bond de 100 a 110 cela creuse un plus bas a 94,9 — et un test cense
    verifier l'objectif touchait en fait le stop. Ici les hauts et les bas sont
    poses a la main.
    """
    n = len(closes)
    if n == 0:
        return md.Bars([], [], [], [], [], [])
    hauts = hauts or [c * 1.002 for c in closes]
    bas = bas or [c * 0.998 for c in closes]
    return md.Bars([ts0 + i * step for i in range(n)], list(closes),
                   list(hauts), list(bas), list(closes), [1_000_000.0] * n)


class TestSilence(unittest.TestCase):
    """Le comportement par défaut est de ne rien dire."""

    def test_nothing_happens_no_alert(self):
        bars = _bars([98.0, 98.5, 97.0, 98.2])
        self.assertIsNone(wl.inspect(_plan(), bars))

    def test_a_closed_plan_is_never_revisited(self):
        item = _plan(triggered=True, closed=True, outcome=wl.STOPPE)
        self.assertIsNone(wl.inspect(item, _bars([200.0] * 4)))

    def test_no_data_is_silence_not_a_guess(self):
        self.assertIsNone(wl.inspect(_plan(), None))
        self.assertIsNone(wl.inspect(_plan(), _bars([])))

    def test_a_move_before_the_plan_existed_does_not_count(self):
        """Sans filtre temporel, un plan dont l'entrée est sous le plus haut de
        la veille serait « déclenché » dès la première vérification."""
        avant = _bars([120.0, 121.0, 119.0], ts0=100)   # bien avant created_ts
        self.assertIsNone(wl.inspect(_plan(created_ts=100_000), avant))


class TestDeclenchement(unittest.TestCase):
    def test_crossing_the_entry_fires_once(self):
        item = _plan()
        bars = _bars([98.0, 99.0, 101.0, 100.5],
                     hauts=[98.5, 99.5, 101.5, 101.0],
                     bas=[97.5, 98.5, 99.0, 100.0])
        event = wl.inspect(item, bars)
        self.assertIsNotNone(event)
        self.assertEqual(event.kind, wl.DECLENCHE)
        self.assertTrue(item.triggered)
        # deuxieme passage : plus rien a annoncer
        self.assertIsNone(wl.inspect(item, bars))

    def test_the_stop_wins_over_the_target(self):  # noqa: D401
        """Une séance qui touche l'objectif ET le stop est une perte.

        Annoncer « objectif atteint » alors que la position est morte serait le
        genre de mensonge poli qu'un outil de trading ne peut pas se permettre.
        """
        item = _plan(triggered=True)
        bars = _bars([100.0, 116.0, 94.0], hauts=[101.0, 116.5, 100.0],
                     bas=[99.0, 100.0, 93.0])
        event = wl.inspect(item, bars)
        self.assertEqual(event.kind, wl.STOPPE)
        self.assertTrue(item.closed)

    def test_reaching_the_target_closes_the_plan(self):
        item = _plan(triggered=True)
        event = wl.inspect(item, _bars([100.0, 110.0, 116.0],
                                       hauts=[101.0, 111.0, 116.5],
                                       bas=[99.5, 109.0, 114.0]))
        self.assertEqual(event.kind, wl.OBJECTIF)
        self.assertTrue(item.closed)
        self.assertEqual(item.outcome, wl.OBJECTIF)

    def test_an_untriggered_plan_cannot_be_stopped(self):
        """Sans entrée, il n'y a pas de position : rien à stopper."""
        item = _plan()
        self.assertIsNone(wl.inspect(item, _bars([90.0, 85.0, 80.0],
                                                 bas=[89.0, 84.0, 79.0])))
        self.assertFalse(item.triggered)
        self.assertFalse(item.closed)


class TestPersistance(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            chemin = os.path.join(d, "w.json")
            wl.save(chemin, [_plan(), _plan(ticker="MB", triggered=True)])
            relu = wl.load(chemin)
            self.assertEqual([w.ticker for w in relu], ["REC", "MB"])
            self.assertTrue(relu[1].triggered)

    def test_a_missing_or_broken_file_is_empty_not_fatal(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(wl.load(os.path.join(d, "absent.json")), [])
            casse = os.path.join(d, "casse.json")
            with open(casse, "w") as fh:
                fh.write("{ pas du json")
            self.assertEqual(wl.load(casse), [])

    def test_unknown_fields_do_not_crash_the_load(self):
        with tempfile.TemporaryDirectory() as d:
            chemin = os.path.join(d, "w.json")
            with open(chemin, "w") as fh:
                json.dump([{"ticker": "REC", "champ_inconnu": 42}], fh)
            self.assertEqual(wl.load(chemin)[0].ticker, "REC")

    def test_a_new_scan_never_erases_a_live_plan(self):
        ancien = _plan(triggered=True)
        fusion = wl.merge([ancien], [_plan(entry=999.0), _plan(ticker="MB")])
        par_ticker = {w.ticker: w for w in fusion}
        self.assertTrue(par_ticker["REC"].triggered)
        self.assertEqual(par_ticker["REC"].entry, 100.0)   # l'ancien plan prime
        self.assertIn("MB", par_ticker)

    def test_a_closed_plan_makes_room_for_the_new_one(self):
        mort = _plan(triggered=True, closed=True, outcome=wl.STOPPE)
        fusion = wl.merge([mort], [_plan(entry=200.0)])
        self.assertEqual(len(fusion), 1)
        self.assertEqual(fusion[0].entry, 200.0)


class TestMessage(unittest.TestCase):
    def test_each_kind_has_its_own_message(self):
        item = _plan()
        rendus = {k: tg.format_event(wl.Event(item, k, 101.0))
                  for k in (wl.DECLENCHE, wl.STOPPE, wl.OBJECTIF)}
        self.assertEqual(len(set(rendus.values())), 3)
        for rendu in rendus.values():
            self.assertIn("Recordati SpA", rendu)
            self.assertIn("pas un conseil", rendu.lower())

    def test_the_trigger_message_repeats_the_safety_exit(self):
        rendu = tg.format_event(wl.Event(_plan(), wl.DECLENCHE, 101.0))
        self.assertIn("95", rendu)
        self.assertIn("Sortie de secours", rendu)

    def test_a_loss_is_presented_as_expected_not_as_a_failure(self):
        rendu = tg.format_event(wl.Event(_plan(), wl.STOPPE, 94.0))
        self.assertIn("scénario prévu", rendu)


class TestSince(unittest.TestCase):
    def test_only_bars_after_the_plan_are_kept(self):
        bars = md.Bars([10, 20, 30, 40], [1] * 4, [1] * 4, [1] * 4, [1] * 4, [1] * 4)
        self.assertEqual(wl.since(bars, 25).ts, [30, 40])
        self.assertEqual(wl.since(bars, 10).ts, [10, 20, 30, 40])
        self.assertIsNone(wl.since(bars, 100))

    def test_metadata_survives_the_filter(self):
        bars = md.Bars([10, 20], [1, 1], [1, 1], [1, 1], [1, 1], [1, 1],
                       "Recordati SpA", "EUR")
        vue = wl.since(bars, 10)
        self.assertEqual(vue.name, "Recordati SpA")
        self.assertEqual(vue.currency, "EUR")


if __name__ == "__main__":
    unittest.main()
