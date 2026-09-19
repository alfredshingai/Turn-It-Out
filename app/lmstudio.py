"""LM Studio provider: real model-based perplexity scoring, 100% local.

Uses LM Studio's OpenAI-compatible API (http://localhost:1234/v1):
  - GET  /v1/models        → discover loaded models
  - POST /v1/completions   → echo + logprobs to score the text under a model

Signals produced:
  - **Perplexity**: how surprised the model is by each token. AI text is highly
    predictable (low perplexity); human text is not. This is the core signal
    behind commercial detectors like GPTZero.
  - **Cross-model ratio (Binoculars-inspired)**: the same text scored under two
    *different* local models. AI text is equally predictable to both models;
    human text diverges. The Binoculars paper (ICLR 2024) showed this ratio is
    state-of-the-art zero-shot detection.

Fails soft everywhere: if LM Studio isn't running the caller falls back to
heuristics. Never crashes a scan.
"""
import json
import math
import os
import re
import time
import urllib.request

BASE_URL = os.environ.get("LMSTUDIO_URL", "http://127.0.0.1:1234/v1")
TIMEOUT = 90          # local CPU inference can be slow; be patient
PROBE_TIMEOUT = 1.5
MAX_CHUNKS = 12       # bound total scoring time
CHUNK_CHARS = 1500
MIN_WORDS = 80        # perplexity is meaningful on shorter text than stylometry

# Log-scale perplexity mapping for aiScore (calibratable).
#   ppl <= PPL_FLOOR  → aiScore 1.0   (extremely predictable = almost surely AI)
#   ppl >= PPL_CEIL   → aiScore 0.0
PPL_FLOOR = 6.0
PPL_CEIL = 150.0


def _post(path: str, body: dict, timeout: float = TIMEOUT) -> dict:
    req = urllib.request.Request(
        BASE_URL + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _get(path: str, timeout: float = PROBE_TIMEOUT) -> dict:
    with urllib.request.urlopen(BASE_URL + path, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def available() -> bool:
    """True when LM Studio's server is reachable."""
    try:
        _get("/models")
        return True
    except Exception:
        return False


def models() -> list[str]:
    """All model IDs known to LM Studio (loaded or downloadable catalog)."""
    try:
        data = _get("/models", timeout=5)
        return [m.get("id", "") for m in data.get("data", []) if m.get("id")]
    except Exception:
        return []


def pick_models() -> tuple[str, str | None]:
    """(expert, base) models for perplexity + cross-model scoring."""
    ids = models()
    if not ids:
        return "", None
    expert = os.environ.get("LMSTUDIO_EXPERT") or ids[0]
    base = os.environ.get("LMSTUDIO_BASE") or (ids[1] if len(ids) > 1 else None)
    if base == expert:
        base = None
    return expert, base


# ------------------------------------------------------------------ scoring

def _score_chunk(model: str, chunk: str) -> tuple[float, int]:
    """Return (sum_logprob, n_tokens) for chunk under model via echo+logprobs."""
    r = _post("/completions", {
        "model": model, "prompt": chunk, "max_tokens": 0,
        "echo": True, "logprobs": 1, "temperature": 0,
    })
    choice = (r.get("choices") or [{}])[0]
    lp = (choice.get("logprobs") or {})
    token_lps = lp.get("token_logprobs") or []
    # With echo, the first token has no conditional logprob (None).
    vals = [x for x in token_lps if isinstance(x, (int, float))]
    if not vals:
        raise ValueError("no logprobs in response")
    return sum(vals), len(vals)


def _chunks(text: str) -> list[str]:
    """Split at sentence boundaries into ~CHUNK_CHARS pieces, capped."""
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    chunks, cur = [], ""
    for p in parts:
        if cur and len(cur) + len(p) > CHUNK_CHARS:
            chunks.append(cur)
            cur = p
        else:
            cur = (cur + " " + p).strip()
        if len(chunks) >= MAX_CHUNKS:
            break
    if cur and len(chunks) < MAX_CHUNKS:
        chunks.append(cur)
    return chunks


def _perplexity(model: str, text: str) -> tuple[float, int, bool]:
    """Mean perplexity of text under model. Returns (ppl, tokens, truncated)."""
    total_lp, total_n, n_chunks = 0.0, 0, 0
    for chunk in _chunks(text):
        lp, n = _score_chunk(model, chunk)
        total_lp += lp
        total_n += n
        n_chunks += 1
    if total_n == 0:
        raise ValueError("no tokens scored")
    ppl = math.exp(-total_lp / total_n)
    return ppl, total_n, n_chunks >= MAX_CHUNKS


def ppl_to_ai_score(ppl: float) -> float:
    """Map perplexity to AI likelihood on a log scale."""
    if ppl <= 0:
        return 1.0
    lo, hi = math.log(PPL_FLOOR), math.log(PPL_CEIL)
    x = (math.log(ppl) - lo) / (hi - lo)
    return round(max(0.0, min(1.0, 1.0 - x)), 4)


# ------------------------------------------------------------------- report

def report(text: str) -> dict:
    expert, base = pick_models()
    if not expert:
        raise RuntimeError("LM Studio reachable but no models available")

    ppl_expert, n_tokens, truncated = _perplexity(expert, text)
    ai_score = ppl_to_ai_score(ppl_expert)

    metrics = {
        "perplexity": round(ppl_expert, 2),
        "expertModel": expert,
        "tokensScored": n_tokens,
    }
    classification = _classify_from_score(ai_score)

    # Cross-model ratio (Binoculars-inspired): score under a second model too.
    ratio = None
    if base:
        ppl_base, _, _ = _perplexity(base, text)
        # Divergence between models' surprise. AI text: both low, ratio ≈ 1.
        ratio = round(ppl_base / ppl_expert, 4) if ppl_expert else None
        metrics["perplexitySecondModel"] = round(ppl_base, 2)
        metrics["crossModelRatio"] = ratio
        metrics["baseModel"] = base
        # Ratio close to 1 with both perplexities low = strong AI signal.
        if ppl_base and ppl_expert:
            both_low = (math.log(ppl_expert) + math.log(ppl_base)) / 2
            agreement = max(0.0, min(1.0, 1.0 - (both_low - math.log(PPL_FLOOR)) /
                                           (math.log(PPL_CEIL) - math.log(PPL_FLOOR))))
            ai_score = round(max(ai_score, 0.5 * ai_score + 0.5 * agreement), 4)
            classification = _classify_from_score(ai_score)

    # Paragraph-level breakdown for highlighting (scored independently).
    paragraph_scores = []
    for para in _paragraphs(text):
        if len(para["text"].split()) < 25:
            continue
        try:
            p_ppl, _, _ = _perplexity(expert, para["text"])
            paragraph_scores.append({
                "start": para["start"], "end": para["end"],
                "aiScore": ppl_to_ai_score(p_ppl),
            })
        except Exception:
            continue

    return {
        "provider": "lmstudio",
        "aiScore": ai_score,
        "classification": classification,
        "sentenceScores": paragraph_scores,  # paragraph granularity
        "metrics": metrics,
        "note": ("Local model scoring via LM Studio (perplexity"
                 + (" + cross-model ratio" if base else "")
                 + "). Probabilistic — not proof of misconduct.")
                + (" Truncated to first %d chunks." % MAX_CHUNKS if truncated else ""),
        "modelsUsed": [m for m in (expert, base) if m],
    }


def _paragraphs(text: str) -> list[dict]:
    out, pos = [], 0
    for para in text.split("\n\n"):
        if para.strip():
            start = text.find(para, pos)
            out.append({"start": start, "end": start + len(para), "text": para})
            pos = start + len(para)
    return out


def _classify_from_score(score: float) -> str:
    if score >= 0.8:
        return "likely_ai"
    if score >= 0.5:
        return "mixed"
    if score >= 0.2:
        return "probably_human"
    return "human"


# ----------------------------------------------------------- cached probing

_last_probe: tuple[float, bool] = (0.0, False)


def available_cached(ttl: float = 30.0) -> bool:
    """Probe LM Studio at most once per ttl seconds (scans may be frequent)."""
    global _last_probe
    now = time.time()
    if now - _last_probe[0] < ttl:
        return _last_probe[1]
    ok = available()
    _last_probe = (now, ok)
    return ok
