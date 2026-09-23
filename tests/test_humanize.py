"""Tests for writing enhancer (app/humanize.py + /api/humanize)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import humanize
from app import server


class TestLocalEnhance(unittest.TestCase):
    def test_cliche_replaced(self):
        text = ("Furthermore, the implementation utilizes a comprehensive framework. "
                "Moreover, it will facilitate robust outcomes for many stakeholders "
                "across diverse sectors today.")
        r = humanize.local_enhance(text, "clarity")
        self.assertIn("also", r["enhanced"].lower())
        self.assertNotIn("Furthermore", r["enhanced"])
        self.assertGreater(r["editCount"], 0)
        self.assertTrue(all("reason" in e for e in r["edits"]))

    def test_concise_trims_wordy(self):
        text = "In order to succeed we must make use of many tools at this point in time today."
        r = humanize.local_enhance(text + " Extra words here to pass length.", "concise")
        self.assertIn("use", r["enhanced"].lower())

    def test_natural_contracts(self):
        text = "We do not think it is not working as we cannot verify the results today fully."
        r = humanize.local_enhance(text + " Adding more words to reach minimum length here.", "natural")
        self.assertIn("don't", r["enhanced"].lower())

    def test_formal_expands(self):
        text = "We don't think it's working but we can't verify the full results today yet."
        r = humanize.local_enhance(text + " Adding more words to reach minimum length here.", "formal")
        self.assertIn("do not", r["enhanced"].lower())

    def test_repeated_connective_varied(self):
        text = ("Furthermore, cats are great pets for families everywhere. "
                "Furthermore, dogs are loyal companions for owners everywhere. "
                "Additionally, birds sing sweet songs in the bright morning light.")
        r = humanize.local_enhance(text, "clarity")
        # cliche pass simplifies Furthermore->Also, so count should drop to <=1
        self.assertLessEqual(r["enhanced"].lower().count("furthermore"), 1)

    def test_enhance_validates_length(self):
        with self.assertRaises(ValueError):
            humanize.enhance("hi", mode="clarity", use_lmstudio=False)

    def test_enhance_offline_has_ethics_note(self):
        text = "Furthermore, this utilize a robust framework for testing purposes today fully."
        r = humanize.enhance(text + " Adding filler words to reach minimum word count.", mode="clarity",
                             use_lmstudio=False)
        self.assertEqual(r["provider"], "local-rules")
        self.assertIn("not a bypass", r["note"].lower())


class TestHumanizeEndpoint(unittest.TestCase):
    def test_h_humanize_ok(self):
        ctx = {"body": {"text": ("Furthermore, we utilize a comprehensive system "
                                 "for checking many documents every single day now."),
                         "mode": "clarity", "useLmStudio": "0"}, "files": {}}
        status, obj = server.h_humanize(ctx)
        self.assertEqual(status, 200)
        self.assertIn("enhanced", obj)
        self.assertIn("edits", obj)
        self.assertIn("note", obj)

    def test_h_humanize_bad_mode(self):
        ctx = {"body": {"text": "hello world foo bar baz qux today", "mode": "sneaky"}, "files": {}}
        with self.assertRaises(server.ApiError):
            server.h_humanize(ctx)

    def test_h_humanize_too_short(self):
        ctx = {"body": {"text": "hi", "mode": "clarity"}, "files": {}}
        with self.assertRaises(server.ApiError):
            server.h_humanize(ctx)


if __name__ == "__main__":
    unittest.main()
