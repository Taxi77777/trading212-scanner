"""Chaîne complète hors ligne : données -> score -> phase -> plan -> message."""
import unittest

from stockscan import market_data as md
from stockscan import phases as ph
from stockscan import scan as sn
from stockscan import telegram as tg
from stockscan import universe as uni

from tests import synth


class FakeData(md.MarketData):
    """Remplace le réseau par des séries déterministes.

    Le scan doit être testable sans Yahoo : sinon la moindre coupure rend la
    suite rouge pour une raison qui n'a rien à voir avec le code.
    """

    def __init__(self, winners=(), broken=()):
        super().__init__(per_second=0.0)
        self.winners = set(winners)
        self.broken = set(broken)
        self.asked: list[str] = []

    def fetch(self, symbol, interval, range_):
        self.asked.append(symbol)
        self.stats["calls"] += 1
        if symbol in self.broken:
            self.stats["empty"] += 1
            return None
        self.stats["ok"] += 1
        if symbol == "^VIX":
            return synth.build([16.0] * 300)
        if symbol.startswith("^") or symbol.endswith((".MI", ".SW")) and symbol[0] == "^":
            return synth.build(synth.uptrend(400, daily=0.0006))
        if symbol in self.winners:
            closes = synth.tightening_base(rally=260, base=70)
            vols = synth.volumes_for(closes, dry_from=270)
            return synth.build(closes, vols)
        return synth.build(synth.downtrend(400))


INDEXES = {m.index_symbol for m in uni.MARKETS.values()}


class FakeDataWithIndexes(FakeData):
    def fetch(self, symbol, interval, range_):
        if symbol in INDEXES:
            self.asked.append(symbol)
            self.stats["calls"] += 1
            self.stats["ok"] += 1
            return synth.build(synth.uptrend(400, daily=0.0006))
        return super().fetch(symbol, interval, range_)


class TestScan(unittest.TestCase):
    def _run(self, **kw):
        stocks = uni.universe(("US",))[:8]
        winner = stocks[0].symbol
        data = FakeDataWithIndexes(winners={winner})
        cfg = sn.Config(markets=("US",), limit=8, use_ai=False,
                        use_fundamentals=False, per_second=0.0, **kw)
        return stocks, data, sn.run(cfg, data=data, now="20/08/2026")

    def test_scan_runs_offline_and_counts_everything(self):
        stocks, data, (summary, kept) = self._run()
        self.assertEqual(summary.analysed, 8)
        self.assertEqual(summary.failed, 0)
        self.assertEqual(sum(summary.counts.values()), 8)
        self.assertIsNotNone(summary.regime)

    def test_one_request_per_stock_plus_the_indexes(self):
        stocks, data, _ = self._run()
        stock_calls = [s for s in data.asked if not s.startswith("^")]
        self.assertEqual(len(stock_calls), 8)
        self.assertEqual(len(stock_calls), len(set(stock_calls)))

    def test_downtrends_are_never_kept(self):
        stocks, _data, (_summary, kept) = self._run()
        self.assertTrue(all(c.phase.name in sn.ACTIONABLE for c in kept))
        self.assertTrue(all(c.trend.direction != "BAISSIERE" for c in kept))

    def test_broken_symbols_are_counted_not_crashing(self):
        stocks = uni.universe(("US",))[:8]
        data = FakeDataWithIndexes(broken={s.symbol for s in stocks[:3]})
        summary, kept = sn.run(sn.Config(markets=("US",), limit=8, use_ai=False,
                                         use_fundamentals=False, per_second=0.0),
                               data=data, now="20/08/2026")
        self.assertEqual(summary.failed, 3)
        self.assertEqual(summary.fetched, 5)

    def test_high_threshold_keeps_nothing_rather_than_lowering_the_bar(self):
        _s, _d, (summary, kept) = self._run(min_score=99.0)
        self.assertEqual(kept, [])
        self.assertEqual(summary.kept, 0)

    def test_ranking_puts_prebreakout_first(self):
        def cand(name, pre, total):
            c = type("C", (), {})()
            c.phase = ph.Phase(name=name)
            c.score = type("S", (), {"prebreakout": pre, "total": total})()
            return c
        rows = [cand(ph.BREAKOUT, 90, 90), cand(ph.EARLY, 95, 95),
                cand(ph.PRE_BREAKOUT, 50, 50), cand(ph.RETEST, 80, 80)]
        rows.sort(key=sn.rank_key)
        self.assertEqual([c.phase.name for c in rows],
                         [ph.PRE_BREAKOUT, ph.RETEST, ph.BREAKOUT, ph.EARLY])


class TestTelegramFormatting(unittest.TestCase):
    def _candidates(self):
        stocks = uni.universe(("US",))[:8]
        data = FakeDataWithIndexes(winners={stocks[0].symbol})
        return sn.run(sn.Config(markets=("US",), limit=8, use_ai=False,
                                use_fundamentals=False, per_second=0.0,
                                min_score=0.0, min_prebreakout=0.0),
                      data=data, now="20/08/2026")

    def test_no_signal_produces_a_message_not_silence(self):
        summary, _ = self._candidates()
        text = tg.format_no_trade(summary)
        self.assertIn("Aucun signal", text)
        self.assertIn("Ne rien faire est une décision", text)

    def test_report_never_promises_a_rise(self):
        summary, kept = self._candidates()
        blob = "\n".join(tg.build_report(summary, kept)).lower()
        for banned in ("va monter", "va grimper", "garanti", "certain de"):
            self.assertNotIn(banned, blob)

    def test_messages_respect_the_telegram_limit(self):
        summary, kept = self._candidates()
        for message in tg.build_report(summary, kept):
            self.assertLessEqual(len(message), tg.LIMIT)

    def test_html_is_escaped(self):
        summary, kept = self._candidates()
        if not kept:
            self.skipTest("aucun candidat")
        kept[0].ticker = "A<B>&C"
        text = tg.format_candidate(kept[0])
        self.assertIn("A&lt;B&gt;&amp;C", text)

    def test_secrets_are_redacted(self):
        original = tg.BOT_TOKEN, tg.CHAT_ID
        tg.BOT_TOKEN, tg.CHAT_ID = "123456:AAsecret", "999888"
        try:
            out = tg.redact("erreur sur https://api.telegram.org/bot123456:AAsecret/"
                            "sendMessage chat 999888")
            self.assertNotIn("AAsecret", out)
            self.assertNotIn("999888", out)
            self.assertNotIn("123456", out)
        finally:
            tg.BOT_TOKEN, tg.CHAT_ID = original

    def test_send_is_refused_without_secrets(self):
        original = tg.BOT_TOKEN, tg.CHAT_ID
        tg.BOT_TOKEN, tg.CHAT_ID = "", ""
        try:
            self.assertFalse(tg.send("x")["ok"])
            self.assertTrue(tg.send("x", dry_run=True)["ok"])
        finally:
            tg.BOT_TOKEN, tg.CHAT_ID = original

    def test_the_report_always_says_no_order_is_sent(self):
        """La mention doit figurer dans le message RECU, pas dans chaque bloc.

        Elle a ete deplacee du bloc candidat vers le pied du rapport quand le
        message a ete simplifie : la garantie porte sur ce que l'utilisateur
        lit, pas sur un fragment interne.
        """
        summary, kept = self._candidates()
        blob = "\n".join(tg.build_report(summary, kept)).lower()
        self.assertIn("aucun ordre", blob)
        self.assertIn("pas un conseil d'investissement", blob)

    def test_the_no_signal_message_says_it_too(self):
        summary, _ = self._candidates()
        self.assertIn("aucun ordre", tg.format_no_trade(summary).lower())


if __name__ == "__main__":
    unittest.main()

    def test_the_message_never_reads_as_an_order(self):
        """Un utilisateur a ouvert une position en croyant recevoir un ordre.

        « Entrée si le cours dépasse X » se lit comme une consigne. Le message
        doit dire explicitement qu'il ne recommande rien, et le dire EN HAUT,
        pas seulement dans la mention légale de bas de page.
        """
        summary, kept = self._candidates()
        blob = "\n".join(tg.build_report(summary, kept)).lower()
        self.assertIn("ne dit pas d'acheter", blob)
        self.assertIn("pas une recommandation", blob)
        self.assertIn("1 signal sur 3", blob)
        for consigne in ("entrée si le cours dépasse", "tu gagnes", "tu perds"):
            self.assertNotIn(consigne, blob, f"formulation impérative : {consigne}")

    def test_the_warning_comes_before_the_first_candidate(self):
        summary, kept = self._candidates()
        if not kept:
            self.skipTest("aucun candidat")
        blob = "\n".join(tg.build_report(summary, kept))
        self.assertLess(blob.index("ne dit pas d'acheter"),
                        blob.index(kept[0].ticker),
                        "l'avertissement doit précéder les valeurs")
