"""End-to-end offline run of the full v7 pipeline.

No network: market data, calendar, Cloudflare and Telegram are all stubbed.
Proves the wiring engine -> coherence -> AI -> filter -> medal -> Telegram.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import telegram_signals as tg

tg.TELEGRAM_BOT_TOKEN = tg.TELEGRAM_BOT_TOKEN or "test-token"
tg.TELEGRAM_CHAT_ID = tg.TELEGRAM_CHAT_ID or "test-chat"

import forex_ai_judge
import forex_quality
import run_forex_v7 as v7
from tests import synthetic

scanner = v7.scanner


class PipelineHarness(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.sent: list[str] = []
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

        self._saved = {
            "state_file": tg.STATE_FILE,
            "send": tg.telegram_send,
            "fetch": v7._fetch_orig,
            "calendar": scanner.calendar_events,
            "session": scanner.session_name,
            "judge": forex_ai_judge.judge_signal,
            "configured": forex_ai_judge.configured,
            "connectivity": forex_ai_judge.check_connectivity,
        }
        tg.STATE_FILE = Path(self.tmp.name) / "state.json"
        tg.telegram_send = lambda text: (self.sent.append(text), True)[1]
        scanner.calendar_events = lambda: []
        scanner.session_name = lambda: "LONDRES + NEW YORK"
        v7._fetch_orig = self.fake_fetch
        forex_ai_judge.configured = lambda: True
        forex_ai_judge.check_connectivity = lambda: {
            "connected": True, "model": "stub", "http": 200, "error": "",
            "account_id_present": True, "api_token_present": True,
            "gateway": "stub", "json_ok": True, "answer_ok": True, "verdict_ok": True,
        }
        self.ai_result = {"available": True, "verdict": "CONFIRME", "confidence": 88,
                          "contradiction": False, "reason": "structure alignée"}
        self.ai_calls: list[str] = []
        forex_ai_judge.judge_signal = self.fake_judge

        def restore():
            tg.STATE_FILE = self._saved["state_file"]
            tg.telegram_send = self._saved["send"]
            v7._fetch_orig = self._saved["fetch"]
            scanner.calendar_events = self._saved["calendar"]
            scanner.session_name = self._saved["session"]
            forex_ai_judge.judge_signal = self._saved["judge"]
            forex_ai_judge.configured = self._saved["configured"]
            forex_ai_judge.check_connectivity = self._saved["connectivity"]
        self.addCleanup(restore)

    # -- stubs ------------------------------------------------------------ #
    def fake_judge(self, sig):
        self.ai_calls.append(sig.pair)
        return dict(self.ai_result)

    def fake_fetch(self, symbol, interval, range_):
        if symbol in (scanner.DXY, scanner.US10Y, scanner.VIX, scanner.SPY):
            return synthetic.market(-1)[symbol]
        direction = 1 if symbol.startswith(("EUR", "GBP", "AUD")) else -1
        data = synthetic.frames(direction)
        return {"1d": data["d1"], "1h": data["h1"], "15m": data["m15"]}.get(interval)

    def signals(self):
        return [m for m in self.sent if "SIGNAL FOREX" in m]

    def diagnostic(self):
        return next(m for m in self.sent if "DIAGNOSTIC FOREX V7" in m)


class TestFullRun(PipelineHarness):
    def test_run_produces_ranked_medalled_alerts(self):
        rc = v7.main()
        self.assertEqual(rc, 0)

        signals = self.signals()
        self.assertTrue(signals, "aucun signal produit par la chaîne complète")
        self.assertLessEqual(len(signals), scanner.MAX_ALERTS)

        # Medals are awarded in quality order, gold first and only once.
        medals = [m.split(" — ")[0].split(" ", 1)[1] for m in signals]
        self.assertEqual(medals[0], "🥇 OR")
        self.assertEqual(len(medals), len(set(medals)))

        for text in signals:
            self.assertIn("🤖 IA Cloudflare Qwen3 : CONFIRME (88%)", text)
            self.assertIn("Qualité globale :", text)
            self.assertIn("⚠️ Analyse uniquement", text)

        self.assertIn("DIAGNOSTIC FOREX V7", self.diagnostic())
        self.assertIn("Cloudflare AI : CONNECTÉE", self.diagnostic())

    def test_ai_is_called_only_on_the_shortlist(self):
        v7.main()
        self.assertLessEqual(len(self.ai_calls), v7.AI_MAX_CALLS)
        self.assertLess(len(self.ai_calls), len(scanner.PAIRS))

    def test_real_contradiction_blocks_every_alert(self):
        self.ai_result = {"available": True, "verdict": "CONTRADICTION", "confidence": 90,
                          "contradiction": True, "reason": "D1 opposé"}
        v7.main()
        self.assertEqual(self.signals(), [])
        self.assertIn("Contradiction IA :", self.diagnostic())

    def test_offline_ai_does_not_block(self):
        self.ai_result = {"available": False, "verdict": "INDISPONIBLE", "confidence": 0,
                          "contradiction": False, "reason": "timeout"}
        v7.main()
        signals = self.signals()
        self.assertTrue(signals, "une IA indisponible ne doit jamais bloquer le moteur")
        self.assertIn("🤖 IA Cloudflare Qwen3 : INDISPONIBLE", signals[0])

    def test_malformed_ai_answer_does_not_block(self):
        forex_ai_judge.judge_signal = lambda sig: forex_ai_judge.normalise_result(
            forex_ai_judge.json_from_text("Okay, let me think about this signal"))
        v7.main()
        self.assertTrue(self.signals())
        self.assertIn("INDISPONIBLE", self.signals()[0])

    @staticmethod
    def _pairs_of(messages):
        return {m.splitlines()[0].rsplit(" — ", 1)[-1] for m in messages}

    def test_cooldown_never_resends_the_same_signal(self):
        v7.main()
        first = self.signals()
        self.assertGreater(len(first), 0)
        first_pairs = self._pairs_of(first)

        self.sent.clear()
        self.ai_calls.clear()
        v7.main()
        second_pairs = self._pairs_of(self.signals())

        self.assertFalse(first_pairs & second_pairs,
                         f"signal réémis pendant le cooldown: {first_pairs & second_pairs}")
        self.assertNotIn("Cooldown actif : 0", self.diagnostic())
        # A cooled-down candidate must never consume a Cloudflare call.
        self.assertFalse(first_pairs & set(self.ai_calls))

    def test_cooldown_mutes_everything_once_the_book_is_covered(self):
        for _ in range(12):
            v7.main()
            self.sent.clear()
            self.ai_calls.clear()
        v7.main()
        self.assertEqual(self.signals(), [],
                         "toutes les paires sont en cooldown : aucun envoi attendu")
        self.assertEqual(self.ai_calls, [], "appels IA gaspillés pendant le cooldown")

    def test_no_data_yields_zero_signals_and_a_reason(self):
        v7._fetch_orig = lambda symbol, interval, range_: None
        v7.main()
        self.assertEqual(self.signals(), [])
        self.assertIn("Données insuffisantes :", self.diagnostic())

    def test_news_blackout_yields_zero_signals_and_a_reason(self):
        import time
        now = time.time()
        scanner.calendar_events = lambda: [
            {"impact": "High", "currency": ccy, "timestamp": now + 300}
            for ccy in ("USD", "EUR", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD")
        ]
        v7.main()
        self.assertEqual(self.signals(), [])
        diagnostic = self.diagnostic()
        self.assertIn("News high impact bloquante :", diagnostic)
        self.assertNotIn("News high impact bloquante : 0", diagnostic)

    def test_no_secret_is_ever_emitted(self):
        v7.main()
        blob = "\n".join(self.sent)
        for forbidden in ("CLOUDFLARE_API_TOKEN=", "Bearer ", "TELEGRAM_BOT_TOKEN="):
            self.assertNotIn(forbidden, blob)

    def test_state_file_is_written_and_prunable(self):
        v7.main()
        self.assertTrue(tg.STATE_FILE.exists())
        state = json.loads(tg.STATE_FILE.read_text(encoding="utf-8"))
        self.assertTrue(any(k.startswith("FXV3:") for k in state))


if __name__ == "__main__":
    unittest.main()
