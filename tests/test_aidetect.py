"""Tests for AI-writing detection (heuristic provider + mocked GPTZero client)."""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import aidetect

HUMANISH = (
    "Honestly, I didn't expect the experiment to fail — but it did, spectacularly. "
    "We'd spent three weeks on the setup, and my lab partner kept saying it'd be fine. "
    "It wasn't fine. The sensor drifted, the baseline wobbled all over the place, and by "
    "hour six we were basically guessing. Maybe that's science? You try something, it "
    "breaks, you write down what happened, and then you kind of stumble into the actual "
    "question you should've been asking from the start. That's what this paper is: a record "
    "of stumbling. I think the drift matters more than the result, honestly, because it "
    "tells us the apparatus can't hold calibration past hour four without active cooling, "
    "and nobody had checked that before us. So we didn't answer the original question at "
    "all. We answered a better one, more or less by accident, which is probably how most "
    "of science actually works when you're honest about it. I'd still want to rerun it "
    "though, ideally with a fresh sensor, because this whole mess taught me something "
    "funny: the failures were more interesting than the result we were chasing. Honestly "
    "that's the part I'd keep if I could only keep one page of the lab notebook."
)

AIISH = (
    "The implementation of renewable energy systems represents a significant advancement "
    "in contemporary environmental policy frameworks. Furthermore, the integration of "
    "sustainable practices demonstrates considerable potential for widespread adoption "
    "across multiple sectors. Additionally, stakeholders must consider various factors "
    "when evaluating implementation strategies. Moreover, comprehensive planning ensures "
    "effective deployment of resources in accordance with established guidelines. "
    "Consequently, organizations can achieve measurable improvements in operational "
    "efficiency. Subsequently, these improvements contribute to broader sustainability "
    "objectives that benefit society. Ultimately, continued investment in renewable "
    "infrastructure will facilitate long-term environmental goals and promote resilient "
    "economic development for future generations across the global community. Therefore, "
    "policymakers should prioritize coordinated regulatory frameworks and sustained "
    "funding mechanisms. Nevertheless, significant challenges remain regarding grid "
    "integration and storage capacity. Accordingly, strategic planning must incorporate "
    "flexible deployment schedules and adaptive management practices. However, the "
    "fundamental transition toward renewable systems appears both inevitable and "
    "increasingly cost-effective across diverse geographic and economic contexts."
)

# AI-generated then aggressively synonym-swapped / connective-dense: the
# signature a word-spinner leaves behind.
PARAPHRASED = (
    "The execution of sustainable power frameworks constitutes a meaningful progression "
    "in present-day ecological governance structures. Furthermore, the incorporation of "
    "sustainable methodologies exhibits notable capability for extensive implementation "
    "throughout numerous domains. Additionally, participants ought to weigh assorted "
    "elements when assessing execution roadmaps. Moreover, thorough orchestration "
    "guarantees proficient allocation of assets in keeping with recognized standards. "
    "Consequently, establishments can realize quantifiable gains in functional "
    "effectiveness. Subsequently, these gains advance wider ecological targets that "
    "aid society. Ultimately, persistent funding in sustainable foundations will "
    "enable enduring ecological ambitions and support sturdy financial expansion for "
    "forthcoming populations throughout the worldwide arena. Therefore, decision "
    "makers ought to emphasize synchronized supervisory frameworks and steady "
    "financing instruments. Nonetheless, considerable obstacles persist concerning "
    "grid incorporation and reserve capability. Accordingly, tactical orchestration "
    "needs to embrace adaptable roll-out calendars and responsive oversight routines."
)


class TestHeuristicDetector(unittest.TestCase):
    def test_uniform_formal_text_scores_higher_than_voicey_text(self):
        ai = aidetect.heuristic_report(AIISH, min_words=50)
        human = aidetect.heuristic_report(HUMANISH, min_words=50)
        self.assertIsNotNone(ai["aiScore"])
        self.assertIsNotNone(human["aiScore"])
        self.assertGreater(ai["aiScore"], human["aiScore"])
        self.assertGreaterEqual(ai["aiScore"], 0.3)
        self.assertLessEqual(human["aiScore"], 0.75)

    def test_short_text_returns_insufficient(self):
        r = aidetect.heuristic_report("Too short.", min_words=50)
        self.assertIsNone(r["aiScore"])
        self.assertEqual(r["classification"], "insufficient_text")

    def test_sentence_scores_cover_qualifying_sentences(self):
        r = aidetect.heuristic_report(HUMANISH, min_words=50)
        sents = [s for s in aidetect.doc_sentences(HUMANISH)
                 if aidetect.is_prose_sentence(s["text"])]
        self.assertEqual(len(r["sentenceScores"]), len(sents))
        for s in r["sentenceScores"]:
            self.assertTrue(0.0 <= s["aiScore"] <= 1.0)
            self.assertLessEqual(s["start"], s["end"])

    def test_bypasser_signature_detected(self):
        spun = aidetect.heuristic_report(PARAPHRASED, min_words=50)
        plain = aidetect.heuristic_report(AIISH, min_words=50)
        self.assertGreaterEqual(spun["bypasserScore"], plain["bypasserScore"])
        self.assertGreaterEqual(spun["bypasserScore"], 0.5)
        self.assertEqual(spun["classification"], "ai_paraphrased")

    def test_asterisk_range_flagged(self):
        self.assertTrue(aidetect.asterisked(0.10))
        self.assertTrue(aidetect.asterisked(0.19))
        self.assertFalse(aidetect.asterisked(0.20))
        self.assertFalse(aidetect.asterisked(0.0))
        self.assertFalse(aidetect.asterisked(None))

    def test_qualifying_filter_skips_lists_and_code(self):
        mixed = (
            "The theory of plate tectonics explains continental drift across geological "
            "time through mantle convection currents. "
            "- bullet one about drift. "
            "- bullet two about boundaries. "
            "1. numbered item here. "
            "Wegener's proposal was ridiculed for decades before evidence from paleomagnetic "
            "studies vindicated his continental displacement hypothesis beyond reasonable "
            "scientific doubt, reshaping geology forever. "
            "Seafloor spreading at mid-ocean ridges provided the mechanism Wegener lacked, "
            "finally explaining how continents could move across the planet's surface. "
            "def hack(x): return x\n"
            "# Section heading\n"
            "| col1 | col2 |"
        )
        r = aidetect.heuristic_report(mixed, min_words=20)
        # Qualifying: tectonics + Wegener + seafloor. Rejected: 2 bullets,
        # the numbered item (splits as "1." + short fragment), and the
        # trailing code/heading/table block.
        self.assertEqual(r["qualifyingSentences"], 3)
        self.assertEqual(r["totalSentences"], 8)

    def test_classification_bands(self):
        self.assertEqual(aidetect._classify(0.9), "likely_ai")
        self.assertEqual(aidetect._classify(0.6), "mixed")
        self.assertEqual(aidetect._classify(0.3), "probably_human")
        self.assertEqual(aidetect._classify(0.1), "human")

    def test_report_has_explainable_metrics(self):
        r = aidetect.heuristic_report(AIISH, min_words=50)
        self.assertIn("burstiness", r["metrics"])
        self.assertIn("bypasserSignals", r["metrics"])
        self.assertIn("note", r)  # disclosure text at top level
        self.assertIn("heuristic", r["note"].lower())


class TestGPTZeroClient(unittest.TestCase):
    def setUp(self):
        os.environ["GPTZERO_API_KEY"] = "test-key-123"
        self.addCleanup(os.environ.pop, "GPTZERO_API_KEY", None)

    def _mock_response(self, doc_body):
        resp = mock.MagicMock()
        resp.read.return_value = json.dumps({"documents": [doc_body]}).encode()
        resp.__enter__.return_value = resp
        return resp

    def _sentence_counts(self):
        return len(aidetect.doc_sentences(HUMANISH))

    def test_parses_document_and_sentences(self):
        body = {
            "average_generated_prob": 0.42,
            "completely_generated_prob": 0.18,
            "document_classification": "HUMAN_ONLY",
            "sentences": [{"generated_prob": 0.1} for _ in range(self._sentence_counts())],
        }
        with mock.patch.object(aidetect.urllib.request, "urlopen",
                               return_value=self._mock_response(body)):
            r = aidetect.gptzero_report(HUMANISH)
        self.assertEqual(r["provider"], "gptzero")
        self.assertEqual(r["classification"], "HUMAN_ONLY")
        self.assertAlmostEqual(r["aiScore"], 0.18, places=3)
        self.assertEqual(len(r["sentenceScores"]), self._sentence_counts())

    def test_missing_key_raises(self):
        os.environ.pop("GPTZERO_API_KEY", None)
        with self.assertRaises(RuntimeError):
            aidetect.gptzero_report(HUMANISH)

    def test_analyze_falls_back_on_api_error(self):
        with mock.patch.object(aidetect.urllib.request, "urlopen",
                               side_effect=OSError("network down")):
            r = aidetect.analyze(AIISH, min_words=50)
        self.assertEqual(r["provider"], "heuristic")
        self.assertIsNotNone(r["aiScore"])

    def test_provider_status_reflects_key(self):
        self.assertEqual(aidetect.provider_status()["mode"], "gptzero")
        os.environ.pop("GPTZERO_API_KEY", None)
        self.assertEqual(aidetect.provider_status()["mode"], "heuristic")


if __name__ == "__main__":
    unittest.main()
