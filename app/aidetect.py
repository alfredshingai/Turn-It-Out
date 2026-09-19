"""AI-writing detection.

Primary provider: GPTZero external API (https://api.gptzero.me/v2/predict/text)
configured via GPTZERO_API_KEY (env var or .env file in project root).

Fallback: a local, explainable heuristic analyzer (burstiness, style markers,
vocabulary predictability) so AI analysis still works without a key. The
fallback is explicitly labeled as heuristic in every report it produces.

Both providers return the same normalized report shape:
  { provider, aiScore (0..1), classification, sentenceScores: [ {start, end, aiScore} ] }
"""
import json
import math
import os
import re
import statistics
import urllib.request

from . import lmstudio

GPTZERO_URL = "https://api.gptzero.me/v2/predict/text"
GPTZERO_TIMEOUT = 30
MIN_WORDS = 300  # Turnitin's prose minimum for reliable AI analysis

# Turnitin's false-positive safeguard: scores in [1,19]% are shown as "*%"
# because they're statistically unreliable. Reuse their threshold.
ASTERISK_MIN, ASTERISK_MAX = 0.01, 0.19

# GPTZero returns sentence probabilities in document order; we re-anchor them
# to char offsets by walking the document's sentences in the same order.
SENT_SPLIT_RE = re.compile(r"[^.!?]+[.!?]*\s*")

# --- qualifying-text filter (Turnitin-style): only prose sentences count ---
# Skip list bullets, numbered lists, table rows, code, headings, citations.
NONPROSE_RE = re.compile(
    r"^\s*(?:"
    r"[-*•·]\s+"                  # bullets
    r"|\d+[.)]\s+"                # numbered lists
    r"|\|.*\|"                    # table rows
    r"|\s*(?:def |class |import |from |for\s|while\s|if\s|#|//|/\*|\{|\})"  # code-ish
    r"|#{1,6}\s"                   # markdown headings
    r")"
)
CITATION_RE = re.compile(r"\([^)]*\b(?:19|20)\d{2}\b[^)]*\)|\[[^\]]*\]")


def is_prose_sentence(s: str) -> bool:
    """Heuristic: prose sentences in long-form writing qualify for AI analysis."""
    text = s.strip()
    if not text:
        return False
    if NONPROSE_RE.match(text):
        return False
    words = _words(text)
    if len(words) < 6:          # fragments, captions, references
        return False
    if CITATION_RE.sub("", text).count(" ") < 5:
        return False
    return True


# --------------------------------------------------------------- .env loading

def load_env() -> None:
    """Populate os.environ from a .env file in the project root (if present)."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if not os.path.isfile(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip().strip('"').strip("'")
                os.environ.setdefault(key, value)
    except OSError:
        pass


def _api_key() -> str:
    return os.environ.get("GPTZERO_API_KEY", "").strip()


def provider_status() -> dict:
    """For /api/ai/status: what is configured, and what will be used.
    Priority: GPTZero key > LM Studio running > heuristics."""
    key = _api_key()
    if key:
        return {
            "mode": "gptzero",
            "label": "GPTZero API",
            "detail": "AI analysis uses the GPTZero API (sentence-level detection).",
            "sentences": True,
        }
    if lmstudio.available_cached():
        expert, base = lmstudio.pick_models()
        detail = ("AI analysis uses local model scoring via LM Studio "
                  f"(perplexity under {expert}"
                  + (f" + cross-model ratio with {base}" if base else "") + ").")
        return {
            "mode": "lmstudio",
            "label": "Local model scoring",
            "detail": detail,
            "sentences": False,  # paragraph-level
            "models": [m for m in (expert, base) if m],
        }
    return {
        "mode": "heuristic",
        "label": "Local heuristics",
        "detail": "No API key and LM Studio not detected — using local heuristic analysis. "
                  "Start LM Studio's local server (Developer tab → Start Server) for "
                  "model-based perplexity detection, or add GPTZERO_API_KEY to .env.",
        "sentences": False,
    }


# ------------------------------------------------------------------ sentences

def doc_sentences(text: str) -> list[dict]:
    """Split into sentences with char offsets: [{start, end, text}]."""
    out: list[dict] = []
    for m in SENT_SPLIT_RE.finditer(text):
        s = m.group(0)
        if s.strip():
            out.append({"start": m.start(), "end": m.end(), "text": s})
    return out


def _words(s: str) -> list[str]:
    return re.findall(r"[A-Za-z']+", s)


# ------------------------------------------------------- heuristic provider

CONTRACTION_RE = re.compile(r"\b\w+('s|'t|'re|'ve|'ll|'d)\b", re.IGNORECASE)
FIRST_PERSON_RE = re.compile(r"\b(I|me|my|mine|we|our|us|you|your)\b")
HEDGE_RE = re.compile(
    r"\b(maybe|perhaps|possibly|arguably|sort of|kind of|i think|i guess|probably|"
    r"it seems|somewhat|fairly|pretty much|actually|honestly|basically|anyway)\b", re.IGNORECASE)


def _burstiness(sents: list[dict]) -> float:
    """CV of sentence lengths (words). AI text tends to have low CV."""
    lens = [len(_words(s["text"])) for s in sents if len(_words(s["text"])) > 0]
    if len(lens) < 4 or statistics.mean(lens) == 0:
        return 0.5  # neutral
    cv = statistics.pstdev(lens) / statistics.mean(lens)
    # Map CV to 0..1: human prose typically CV ~0.5-0.8; AI ~0.2-0.4.
    return max(0.0, min(1.0, (cv - 0.15) / 0.55))


def _style_markers(text: str, words: list[str]) -> dict:
    n = max(1, len(words))
    return {
        "contractions": len(CONTRACTION_RE.findall(text)) / n,
        "firstPerson": len(FIRST_PERSON_RE.findall(text)) / n,
        "hedges": len(HEDGE_RE.findall(text)) / n,
        "typos": _typo_rate(text),
    }


def _typo_rate(text: str) -> float:
    """Proxy for unpolished writing: doubled words, missing spaces, irregular caps."""
    doubled = len(re.findall(r"\b(\w+)\s+\1\b", text, re.IGNORECASE))
    missing_space = len(re.findall(r"[a-z][A-Z]", text))
    stray_caps = len(re.findall(r"\b[a-z]+\b(?=[A-Z])", text))
    return (doubled + missing_space) / max(1, len(text.split()))


def _avg_word_freq_class(words: list[str]) -> float:
    """Predictability proxy: fraction of common short words (high-frequency zipf)."""
    if not words:
        return 0.0
    common = sum(1 for w in words if len(w) <= 5)
    return common / len(words)


# --- bypasser / AI-paraphraser fingerprint ---------------------------------
# Spun text keeps the LLM skeleton (uniform rhythm, formal tone) but swaps
# vocabulary. Typical signatures: scarce punctuation variety, no contractions
# or first person, repeated "connective openers", oddly uniform sentence
# lengths, and unusually rich vocabulary for generic statements.
CONNECTIVE_OPENERS = (
    "moreover", "furthermore", "additionally", "consequently", "subsequently",
    "therefore", "however", "nevertheless", "accordingly", "ultimately",
)


def _bypasser_score(sents: list[dict], words: list[str], style: dict) -> float:
    """0..1 likelihood the text was AI-generated then run through a paraphraser."""
    if not sents:
        return 0.0
    lens = [len(_words(s["text"])) for s in sents]
    mean_len = sum(lens) / len(lens)
    variance = sum((l - mean_len) ** 2 for l in lens) / len(lens)
    uniformity = max(0.0, 1.0 - (variance ** 0.5) / max(mean_len, 1))  # high = uniform

    openers = sum(1 for s in sents
                  if s["text"].strip().lower().startswith(CONNECTIVE_OPENERS))
    opener_ratio = openers / len(sents)

    exclam = text_exclamations = sum(s["text"].count("!") for s in sents)
    punct_variety = min(1.0, exclam / max(1, len(sents) / 4))  # ~0 for spun text

    lex_rich = min(1.0, len(set(w.lower() for w in words)) / max(60, len(words)))

    signals = (
        0.35 * uniformity
        + 0.25 * min(1.0, opener_ratio * 3)
        + 0.15 * (1.0 - punct_variety)
        + 0.15 * (1.0 - min(1.0, style["contractions"] * 40))
        + 0.10 * lex_rich
    )
    return max(0.0, min(1.0, signals))


def heuristic_report(text: str, min_words: int = MIN_WORDS) -> dict:
    all_sents = doc_sentences(text)
    sents = [s for s in all_sents if is_prose_sentence(s["text"])]
    words = _words(" ".join(s["text"] for s in sents))
    if len(words) < min_words:
        return {
            "provider": "heuristic",
            "aiScore": None,
            "classification": "insufficient_text",
            "sentenceScores": [],
            "metrics": {},
            "note": (f"Needs at least {min_words} words of prose (long-form writing) "
                     f"for reliable analysis — Turnitin applies the same rule. "
                     f"Found {len(words)} qualifying words."),
            "qualifyingWords": len(words),
            "totalSentences": len(all_sents),
            "qualifyingSentences": len(sents),
        }

    burst = _burstiness(sents)
    style = _style_markers(text, words)
    freq = _avg_word_freq_class(words)
    bypasser = _bypasser_score(sents, words, style)

    # Weighted blend: burstiness dominates, style markers modulate.
    human_signals = (
        0.45 * burst
        + 0.25 * min(1.0, style["contractions"] * 40)
        + 0.10 * min(1.0, style["hedges"] * 30)
        + 0.10 * min(1.0, style["typos"] * 60)
        + 0.10 * min(1.0, style["firstPerson"] * 25)
    )
    ai_score = round(max(0.0, min(1.0, 1.0 - human_signals)), 4)

    # Sentence scores: distance from document norm, weighted by burstiness deficit.
    sentence_scores = []
    lens = [len(_words(s["text"])) for s in sents]
    mean_len = statistics.mean(lens) if lens else 0
    for s in sents:
        wl = len(_words(s["text"]))
        uniformity = 1.0 - min(1.0, abs(wl - mean_len) / max(mean_len, 1))
        contr = len(CONTRACTION_RE.findall(s["text"])) + len(HEDGE_RE.findall(s["text"]))
        score = max(0.0, min(1.0, 0.7 * ai_score + 0.3 * uniformity - 0.15 * min(2, contr)))
        sentence_scores.append({
            "start": s["start"], "end": s["end"],
            "aiScore": round(score, 3),
        })

    classification = _classify(ai_score)
    if bypasser >= 0.55 and ai_score >= 0.35:
        classification = "ai_paraphrased"

    return {
        "provider": "heuristic",
        "aiScore": ai_score,
        "classification": classification,
        "bypasserScore": round(bypasser, 3),
        "sentenceScores": sentence_scores,
        "metrics": {
            "burstiness": round(burst, 3),
            "contractions": round(style["contractions"], 4),
            "hedges": round(style["hedges"], 4),
            "typos": round(style["typos"], 4),
            "firstPerson": round(style["firstPerson"], 4),
            "commonWordRatio": round(freq, 3),
            "bypasserSignals": round(bypasser, 3),
            "qualifyingSentences": f"{len(sents)}/{len(all_sents)}",
        },
        "note": "Heuristic analysis (burstiness + style signals + bypasser fingerprint) "
                "on qualifying prose only. Not a definitive judgment. "
                "Configure GPTZERO_API_KEY for model-based detection.",
        "qualifyingWords": len(words),
        "totalSentences": len(all_sents),
        "qualifyingSentences": len(sents),
    }


# --------------------------------------------------------------- GPTZero API

def gptzero_report(text: str) -> dict:
    key = _api_key()
    if not key:
        raise RuntimeError("GPTZERO_API_KEY not configured")
    body = json.dumps({"document": text, "version": "2024-11-01"}).encode()
    req = urllib.request.Request(
        GPTZERO_URL, data=body, method="POST",
        headers={"x-api-key": key, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=GPTZERO_TIMEOUT) as resp:
        data = json.loads(resp.read().decode())

    doc = data.get("documents", [{}])[0]
    avg = doc.get("average_generated_prob")
    comp = doc.get("completely_generated_prob")
    score = comp if comp is not None else avg
    score = round(max(0.0, min(1.0, float(score or 0))), 4)

    # Map sentence probabilities back to char offsets by walking doc sentences.
    sents = doc_sentences(text)
    sent_probs = (doc.get("sentences") or [])
    sentence_scores = []
    for i, s in enumerate(sents):
        prob = sent_probs[i].get("generated_prob") if i < len(sent_probs) else None
        sentence_scores.append({
            "start": s["start"], "end": s["end"],
            "aiScore": round(float(prob), 3) if prob is not None else None,
        })

    classification = doc.get("document_classification") or _classify(score)
    return {
        "provider": "gptzero",
        "aiScore": score,
        "classification": classification,
        "sentenceScores": sentence_scores,
        "metrics": {
            "averageGeneratedProb": avg,
            "completelyGeneratedProb": comp,
        },
        "note": "GPTZero API. Probabilistic — not proof of misconduct.",
    }


def _classify(score: float) -> str:
    if score is None:
        return "insufficient_text"
    if score >= 0.8:
        return "likely_ai"
    if score >= 0.5:
        return "mixed"
    if score >= 0.2:
        return "probably_human"
    return "human"


def asterisked(score) -> bool:
    """Turnitin's rule: scores in (0%, 19%] are unreliable → display as *%."""
    return score is not None and ASTERISK_MIN < score <= ASTERISK_MAX


# ------------------------------------------------------------------ entrypoint

def analyze(text: str, min_words: int = MIN_WORDS) -> dict:
    """AI analysis with provider priority: GPTZero key > LM Studio > heuristics.
    Every provider failure degrades softly to the next tier."""
    load_env()
    if _api_key():
        try:
            return gptzero_report(text)
        except Exception:
            pass  # network/quota errors → next tier
    if lmstudio.available_cached():
        try:
            return lmstudio.report(text)
        except Exception:
            pass  # model errors, no loaded model, timeouts → next tier
    return heuristic_report(text, min_words=min_words)
