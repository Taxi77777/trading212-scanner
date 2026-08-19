import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import forex_ai_judge as judge


class TestParsing(unittest.TestCase):
    def test_plain_json(self):
        out = judge.normalise_result(judge.json_from_text(
            '{"verdict":"CONFIRME","confidence":91,"contradiction":false,"reason":"aligné"}'))
        self.assertTrue(out["available"])
        self.assertEqual(out["verdict"], "CONFIRME")
        self.assertEqual(out["confidence"], 91)
        self.assertFalse(out["contradiction"])

    def test_reasoning_block_is_stripped(self):
        raw = ('<think>Let me check {this} and {that} carefully.</think>\n'
               '```json\n{"verdict":"PRUDENCE","confidence":40,'
               '"contradiction":false,"reason":"H1 opposé"}\n```')
        out = judge.normalise_result(judge.json_from_text(raw))
        self.assertEqual(out["verdict"], "PRUDENCE")
        self.assertEqual(out["confidence"], 40)

    def test_truncated_reasoning_is_unavailable_not_contradiction(self):
        """The exact production failure: max_tokens cut the answer mid-thought."""
        raw = ("Okay, let's start by looking at the user's query. They provided a "
               "quantitative signal for NZD/CHF with a BUY side, and I need to review it")
        out = judge.normalise_result(judge.json_from_text(raw))
        self.assertFalse(out["available"])
        self.assertEqual(out["verdict"], "INDISPONIBLE")
        self.assertFalse(out["contradiction"])

    def test_unknown_verdict_is_unavailable(self):
        out = judge.normalise_result({"verdict": "MAYBE", "confidence": 80})
        self.assertFalse(out["available"])
        self.assertEqual(out["verdict"], "INDISPONIBLE")
        self.assertFalse(out["contradiction"])

    def test_contradiction_is_reported(self):
        out = judge.normalise_result(judge.json_from_text(
            '{"verdict":"CONTRADICTION","confidence":88,"contradiction":true,'
            '"reason":"D1 et H4 opposés à la direction"}'))
        self.assertTrue(out["available"])
        self.assertTrue(out["contradiction"])
        self.assertEqual(out["verdict"], "CONTRADICTION")

    def test_contradiction_flag_forces_verdict(self):
        out = judge.normalise_result({"verdict": "CONFIRME", "contradiction": True})
        self.assertEqual(out["verdict"], "CONTRADICTION")
        self.assertTrue(out["contradiction"])

    def test_confidence_is_clamped(self):
        self.assertEqual(judge.normalise_result(
            {"verdict": "CONFIRME", "confidence": 5000})["confidence"], 100)
        self.assertEqual(judge.normalise_result(
            {"verdict": "CONFIRME", "confidence": "abc"})["confidence"], 0)


class TestSecrets(unittest.TestCase):
    def test_redact_masks_token_and_account(self):
        judge.CLOUDFLARE_API_TOKEN = "SUPERSECRETTOKEN123456"
        judge.CLOUDFLARE_ACCOUNT_ID = "abcdef0123456789abcdef0123456789"
        try:
            leaky = ("HTTPSConnectionPool: POST https://api.cloudflare.com/client/v4/"
                     "accounts/abcdef0123456789abcdef0123456789/ai/v1/chat/completions "
                     "Authorization: Bearer SUPERSECRETTOKEN123456")
            clean = judge.redact(leaky)
            self.assertNotIn("SUPERSECRETTOKEN123456", clean)
            self.assertNotIn("abcdef0123456789abcdef0123456789", clean)
            self.assertIn("***", clean)
        finally:
            judge.CLOUDFLARE_API_TOKEN = ""
            judge.CLOUDFLARE_ACCOUNT_ID = ""

    def test_no_secret_in_unconfigured_judgement(self):
        judge.CLOUDFLARE_ACCOUNT_ID = ""
        judge.CLOUDFLARE_API_TOKEN = ""
        out = judge.judge_signal(object())
        self.assertFalse(out["available"])
        self.assertEqual(out["verdict"], "INDISPONIBLE")


if __name__ == "__main__":
    unittest.main()
