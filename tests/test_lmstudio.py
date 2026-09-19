"""Tests for the LM Studio model-scoring provider (all HTTP mocked)."""
import json
import math
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import aidetect, lmstudio

SAMPLE = (
    "The feasibility study examined three deployment scenarios for the new system. "
    "Each scenario differed in hardware cost, migration effort, and expected timeline. "
) * 6


def _echo_response(text: str, lp_per_token: float = -1.0):
    """Mock an OpenAI-style echo+logprobs completion response."""
    n = max(2, len(text) // 4)
    return {
        "choices": [{
            "logprobs": {"token_logprobs": [None] + [lp_per_token] * (n - 1)},
        }],
    }


class TestPerplexityMath(unittest.TestCase):
    def test_score_mapping_bounds(self):
        self.assertEqual(lmstudio.ppl_to_ai_score(1.0), 1.0)
        self.assertEqual(lmstudio.ppl_to_ai_score(10_000), 0.0)
        mid = lmstudio.ppl_to_ai_score(math.sqrt(lmstudio.PPL_FLOOR * lmstudio.PPL_CEIL))
        self.assertAlmostEqual(mid, 0.5, places=2)

    def test_lower_perplexity_is_more_ai(self):
        assert lmstudio.ppl_to_ai_score(8) > lmstudio.ppl_to_ai_score(80) > lmstudio.ppl_to_ai_score(800)


class TestLMStudioProvider(unittest.TestCase):
    def setUp(self):
        # Fresh probe cache per test.
        lmstudio._last_probe = (0.0, False)
        self.addCleanup(setattr, lmstudio, "_last_probe", (0.0, False))

    def _mock_transport(self, exp_lp=-1.0, base_lp=-0.5, n_models=2):
        def fake_urlopen(req, timeout=None):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            body = json.loads(req.data.decode()) if getattr(req, "data", None) else {}
            if url.endswith("/models"):
                payload = {"data": [{"id": f"model-{i}"} for i in range(n_models)]}
                resp = mock.MagicMock()
                resp.read.return_value = json.dumps(payload).encode()
                resp.__enter__.return_value = resp
                return resp
            # completion request
            model = body.get("model", "")
            lp = base_lp if model.endswith("1") else exp_lp
            resp = mock.MagicMock()
            resp.read.return_value = json.dumps(_echo_response(body.get("prompt", ""), lp)).encode()
            resp.__enter__.return_value = resp
            return resp

        return fake_urlopen

    def test_report_with_two_models(self):
        with mock.patch.object(lmstudio.urllib.request, "urlopen",
                               side_effect=self._mock_transport(exp_lp=-1.0, base_lp=-0.5)):
            r = lmstudio.report(SAMPLE)
        self.assertEqual(r["provider"], "lmstudio")
        self.assertIn("perplexity", r["metrics"])
        self.assertIn("crossModelRatio", r["metrics"])
        self.assertIn("crossModelRatio", r["metrics"])
        self.assertTrue(0 <= r["aiScore"] <= 1)
        self.assertEqual(r["modelsUsed"], ["model-0", "model-1"])

    def test_report_with_single_model(self):
        with mock.patch.object(lmstudio.urllib.request, "urlopen",
                               side_effect=self._mock_transport(n_models=1)):
            r = lmstudio.report(SAMPLE)
        self.assertNotIn("crossModelRatio", r["metrics"])
        self.assertEqual(len(r["modelsUsed"]), 1)

    def test_unavailable_falls_through_to_heuristic(self):
        with mock.patch.object(lmstudio.urllib.request, "urlopen",
                               side_effect=OSError("no server")):
            r = aidetect.analyze(SAMPLE, min_words=50)
        self.assertEqual(r["provider"], "heuristic")

    def test_status_when_unavailable(self):
        with mock.patch.object(lmstudio.urllib.request, "urlopen",
                               side_effect=OSError("no server")):
            st = aidetect.provider_status()
        self.assertEqual(st["mode"], "heuristic")

    def test_status_prefers_gptzero_key(self):
        os.environ["GPTZERO_API_KEY"] = "k"
        self.addCleanup(os.environ.pop, "GPTZERO_API_KEY", None)
        st = aidetect.provider_status()
        self.assertEqual(st["mode"], "gptzero")

    def test_chunking_caps_chunks(self):
        chunks = lmstudio._chunks("Sentence one. " * 2000)
        self.assertLessEqual(len(chunks), lmstudio.MAX_CHUNKS)

    def test_paragraph_breakdown_returned(self):
        paras = SAMPLE.strip() + "\n\n" + "Completely different second paragraph here. " * 8
        with mock.patch.object(lmstudio.urllib.request, "urlopen",
                               side_effect=self._mock_transport()):
            r = lmstudio.report(paras)
        self.assertGreaterEqual(len(r["sentenceScores"]), 1)


if __name__ == "__main__":
    unittest.main()
