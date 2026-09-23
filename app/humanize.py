"""Writing enhancer — clarity / readability rewrite for your own draft.

NOT a bypass tool. Purpose: fix stiff, repetitive, AI-sounding prose in text
you have the right to edit (your own essay, report, letter), while keeping
authorship disclosure intact.

Design constraints (matches TurnitOut stack):
- stdlib only, no new dependencies
- works offline via deterministic local rules (always available)
- optionally uses LM Studio locally (http://127.0.0.1:1234/v1) for a
  higher-quality rewrite when the user already runs it for detection.
  No text ever leaves the machine except to that local server.
- never marketed as "evade detection"; every response carries an ethics note
  reminding the user that institutional AI-use policies + disclosure still apply.

Modes:
- clarity  (default): simplify AI-cliche phrases, diversify connectives, trim filler
- concise  : clarity + squeeze wordy constructions
- natural  : clarity + contractions, milder tone (blog / letter voice)
- formal   : clarity + expand contractions, neutral academic tone
"""

import json
import re
import urllib.request

MODES = ("clarity", "concise", "natural", "formal")

ETHICS_NOTE = (
    "Writing enhancer for your own draft — improves clarity, not a bypass tool. "
    "AI-use disclosure and your institution's policies still apply. "
    "Re-check the enhanced text with the Similarity / AI-writing tabs."
)

# --- deterministic phrase maps ------------------------------------------------
# (pattern, replacement, reason). Case-preserving replacement is applied.
CLICHE_MAP = [
    (r"\bdelve\b", "explore", "plain verb over AI-cliche 'delve'"),
    (r"\btapestry\b", "mix", "plain noun over AI-cliche 'tapestry'"),
    (r"\bleverage\b", "use", "plain verb over 'leverage'"),
    (r"\butilize\b", "use", "plain verb over 'utilize'"),
    (r"\butilizes\b", "uses", "plain verb over 'utilizes'"),
    (r"\butilizing\b", "using", "plain verb over 'utilizing'"),
    (r"\bfacilitate\b", "help", "plain verb over 'facilitate'"),
    (r"\bfacilitates\b", "helps", "plain verb over 'facilitates'"),
    (r"\bcomprehensive\b", "full", "simpler adjective"),
    (r"\brobust\b", "strong", "simpler adjective"),
    (r"\bpivotal\b", "key", "simpler adjective"),
    (r"\bmoreover\b", "also", "simpler connective, less stiff"),
    (r"\bfurthermore\b", "also", "simpler connective, less stiff"),
    (r"\badditionally\b", "also", "simpler connective, less stiff"),
    (r"\bconsequently\b", "so", "simpler connective"),
    (r"\bsubsequently\b", "later", "simpler connective"),
    (r"\bnevertheless\b", "still", "simpler connective"),
    (r"\bnonetheless\b", "still", "simpler connective"),
    (r"\baccordingly\b", "so", "simpler connective"),
    (r"\bultimately\b", "in the end", "less grandiose closer"),
    (r"\bit is important to note that\b", "note that", "trim filler"),
    (r"\bit is worth noting that\b", "note that", "trim filler"),
    (r"\bin today'?s fast-paced world\b", "today", "trim filler opening"),
    (r"\bin conclusion\b", "to sum up", "less stiff closer"),
    (r"\ba wide range of\b", "many", "trim filler"),
    (r"\ba variety of\b", "many", "trim filler"),
    (r"\bdue to the fact that\b", "because", "trim wordy construction"),
    (r"\bin order to\b", "to", "trim wordy construction"),
    (r"\bin the event that\b", "if", "trim wordy construction"),
]

WORDY_MAP_CONCISE = [
    (r"\bthere are many\b", "many", "tighter existential"),
    (r"\bthere is a need for\b", "we need", "direct voice"),
    (r"\bmake use of\b", "use", "tighter verb"),
    (r"\bcarry out\b", "do", "tighter verb"),
    (r"\ba large number of\b", "many", "tighter quantifier"),
    (r"\bat this point in time\b", "now", "tighter time phrase"),
]

EXPAND_CONTRACTIONS = {  # formal mode: don't -> do not
    "don't": "do not", "can't": "cannot", "won't": "will not",
    "isn't": "is not", "aren't": "are not", "wasn't": "was not",
    "weren't": "were not", "haven't": "have not", "hasn't": "has not",
    "hadn't": "had not", "wouldn't": "would not", "couldn't": "could not",
    "shouldn't": "should not", "doesn't": "does not", "didn't": "did not",
    "it's": "it is", "that's": "that is", "there's": "there is",
    "we're": "we are", "you're": "you are", "they're": "they are",
    "i'm": "I am", "we've": "we have", "i've": "I have",
}

CONTRACT_MAP = {v: k for k, v in EXPAND_CONTRACTIONS.items() if k in (
    "don't", "can't", "won't", "isn't", "aren't", "wasn't", "weren't",
    "haven't", "hasn't", "wouldn't", "couldn't", "shouldn't", "doesn't", "didn't")}
# add common natural contractions explicitly
CONTRACT_MAP.update({
    "do not": "don't", "cannot": "can't", "will not": "won't",
    "is not": "isn't", "are not": "aren't", "was not": "wasn't",
    "have not": "haven't", "would not": "wouldn't", "could not": "couldn't",
})

CONNECTIVE_STARTERS = ["furthermore", "moreover", "additionally", "consequently",
                       "subsequently", "nevertheless", "accordingly"]
CONNECTIVE_ALT = ["Also", "And", "Still", "So", "Then"]

SENT_SPLIT_RE = re.compile(r"[^.!?]+[.!?]+(?:\s+|$)", re.DOTALL)


def _match_case(replacement: str, original: str) -> str:
    if original.isupper():
        return replacement.upper()
    if original and original[0].isupper():
        return replacement[0].upper() + replacement[1:]
    return replacement


def _apply_map(text: str, mapping, edits: list, edit_type: str) -> str:
    for pattern, repl, reason in mapping:
        def _sub(m, _repl=repl, _reason=reason):
            orig = m.group(0)
            new = _match_case(_repl, orig)
            if new != orig:
                edits.append({"type": edit_type, "original": orig,
                              "suggestion": new, "reason": _reason})
            return new
        text = re.sub(pattern, _sub, text, flags=re.IGNORECASE)
    return text


def _vary_connectives(sentences: list[str], edits: list) -> list[str]:
    """Replace 2nd+ identical stiff sentence-starter with an alternative."""
    seen: dict[str, int] = {}
    out = []
    alt_i = 0
    for s in sentences:
        m = re.match(r"\s*(\w+),", s)
        if m and m.group(1).lower() in CONNECTIVE_STARTERS:
            key = m.group(1).lower()
            seen[key] = seen.get(key, 0) + 1
            if seen[key] >= 2:
                alt = CONNECTIVE_ALT[alt_i % len(CONNECTIVE_ALT)]
                alt_i += 1
                if m.group(1)[0].isupper():
                    pass
                else:
                    alt = alt.lower()
                new_s = s.replace(m.group(1), alt, 1)
                edits.append({"type": "variety", "original": m.group(1),
                              "suggestion": alt,
                              "reason": "vary repeated sentence starter"})
                out.append(new_s)
                continue
        out.append(s)
    return out


def _split_long_sentence(s: str, edits: list) -> str:
    words = s.strip().split()
    if len(words) <= 34:
        return s
    # split at a mid-sentence "; " / ", and " / ". " boundary near the middle
    for sep in ("; ", ", and ", ", but ", " — "):
        if sep in s:
            mid = len(s) // 2
            idx = s.find(sep)
            # pick occurrence closest to middle
            best = min((i for i in range(len(s)) if s.startswith(sep, i)),
                       key=lambda i: abs(i - mid), default=-1)
            if best > 0:
                left, right = s[:best].rstrip(",; "), s[best + len(sep):]
                right = right[0].upper() + right[1:] if right else right
                edits.append({"type": "split", "original": s.strip()[:60] + "…",
                              "suggestion": "split long sentence in two",
                              "reason": "shorter sentences read more naturally"})
                joiner = ". " if not sep.startswith(";") else ". "
                return left.rstrip() + joiner + right.lstrip()
    return s


def _sentence_stats(text: str) -> dict:
    sents = [s for s in SENT_SPLIT_RE.findall(text) if s.strip()]
    words = text.split()
    avg = round(len(words) / max(1, len(sents)), 1)
    connectives = sum(1 for _ in re.finditer(
        r"\b(furthermore|moreover|additionally|consequently|subsequently|nevertheless|accordingly)\b",
        text, flags=re.IGNORECASE))
    return {"sentences": len(sents), "words": len(words),
            "avgSentenceWords": avg, "stiffConnectives": connectives}


def local_enhance(text: str, mode: str = "clarity") -> dict:
    """Deterministic offline rewrite. Returns {enhanced, edits, stats}."""
    if mode not in MODES:
        mode = "clarity"
    edits: list[dict] = []
    out = text

    out = _apply_map(out, CLICHE_MAP, edits, "cliche")
    if mode in ("clarity", "concise", "natural", "formal"):
        pass  # base cliche pass applies to all modes
    if mode == "concise":
        out = _apply_map(out, WORDY_MAP_CONCISE, edits, "concise")

    # contractions per mode
    if mode == "natural":
        for full, short in CONTRACT_MAP.items():
            pat = r"\b" + re.escape(full) + r"\b"

            def _sub(m, _short=short):
                orig = m.group(0)
                new = _match_case(_short, orig)
                if new != orig:
                    edits.append({"type": "tone", "original": orig,
                                  "suggestion": new,
                                  "reason": "contraction for natural voice"})
                return new
            out = re.sub(pat, _sub, out, flags=re.IGNORECASE)
    elif mode == "formal":
        for short, full in EXPAND_CONTRACTIONS.items():
            pat = r"\b" + re.escape(short) + r"\b"

            def _sub2(m, _full=full):
                orig = m.group(0)
                new = _match_case(_full, orig)
                if new != orig:
                    edits.append({"type": "tone", "original": orig,
                                  "suggestion": new,
                                  "reason": "expanded form for formal tone"})
                return new
            out = re.sub(pat, _sub2, out, flags=re.IGNORECASE)

    # sentence-level variety + splitting
    sents = SENT_SPLIT_RE.findall(out)
    if sents:
        sents = _vary_connectives(sents, edits)
        sents = [_split_long_sentence(s, edits) for s in sents]
        # rebuild preserving trailing whitespace style loosely
        out = " ".join(s.strip() for s in sents if s.strip())

    # whitespace cleanup
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out).strip()

    before = _sentence_stats(text)
    after = _sentence_stats(out)
    return {"enhanced": out, "edits": edits[:60],
            "editCount": len(edits),
            "stats": {"before": before, "after": after}}


# --- optional LM Studio rewrite (local only, fails soft) ----------------------

LM_URL = "http://127.0.0.1:1234/v1"
LM_TIMEOUT = 60.0

MODE_PROMPT = {
    "clarity": "Rewrite for clarity: simple words, varied sentence starts, same meaning.",
    "concise": "Rewrite more concisely: cut filler, keep all facts, same meaning.",
    "natural": "Rewrite in a natural personal voice with contractions, same meaning.",
    "formal": "Rewrite in neutral formal tone without contractions, same meaning.",
}


def lmstudio_enhance(text: str, mode: str = "clarity") -> str:
    """Rewrite via local LM Studio chat API. Raises on any failure."""
    instruction = MODE_PROMPT.get(mode, MODE_PROMPT["clarity"])
    body = {
        "messages": [
            {"role": "system", "content":
             "You edit the user's own draft. " + instruction +
             " Only return the rewritten text, no commentary. Keep length similar."},
            {"role": "user", "content": text[:6000]},
        ],
        "temperature": 0.7,
        "max_tokens": max(256, min(2000, len(text.split()) * 2)),
    }
    req = urllib.request.Request(
        LM_URL + "/chat/completions", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=LM_TIMEOUT) as resp:
        data = json.loads(resp.read().decode())
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    out = (msg.get("content") or "").strip()
    if not out:
        raise ValueError("empty LM Studio response")
    return out


def enhance(text: str, mode: str = "clarity", use_lmstudio: bool = True) -> dict:
    """Main entry: try LM Studio first (local), fall back to rules.

    Always returns {enhanced, edits, provider, note, mode}.
    """
    if mode not in MODES:
        mode = "clarity"
    words = len(text.split())
    if words < 5:
        raise ValueError("Need at least 5 words to enhance")
    if words > 30000:
        raise ValueError("Document too long (30,000 word max)")

    local = local_enhance(text, mode)
    provider = "local-rules"
    enhanced, edits = local["enhanced"], local["edits"]

    if use_lmstudio:
        try:
            lm_out = lmstudio_enhance(text, mode)
            # re-run rule stats on LM output for transparency, keep LM text
            lm_stats_before = _sentence_stats(text)
            lm_stats_after = _sentence_stats(lm_out)
            return {"enhanced": lm_out, "edits": edits,
                    "editCount": len(edits), "provider": "lmstudio",
                    "mode": mode,
                    "stats": {"before": lm_stats_before, "after": lm_stats_after},
                    "note": "Local LM Studio rewrite + rule-based edit list. " + ETHICS_NOTE}
        except Exception:
            pass  # fail soft to deterministic rules

    return {"enhanced": enhanced, "edits": edits,
            "editCount": len(edits), "provider": provider, "mode": mode,
            "stats": local["stats"],
            "note": "Offline rule-based rewrite (LM Studio not running). " + ETHICS_NOTE}
