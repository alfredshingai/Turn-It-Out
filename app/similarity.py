"""TurnitOut similarity engine.

Turnitin-style fingerprinting pipeline:
  1. Normalize text, tokenize to words while keeping char offsets into the
     ORIGINAL text so reports can highlight exact spans.
  2. For each source doc, find k-gram (default 6-word) seed matches and greedily
     extend them into maximal word spans.
  3. Filter tiny/generic overlaps (stopword-heavy short matches).
  4. Score: per-source similarity = matched words / doc words; overall = union
     of matched chars / total chars.
"""
import re
from dataclasses import dataclass, field

WORD_RE = re.compile(r"[a-z0-9']+")
K = 6                 # words per fingerprint (k-gram)
MIN_SPAN_WORDS = 5    # spans shorter than this are dropped unless contentful
STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "at",
    "by", "from", "as", "is", "are", "was", "were", "be", "been", "it", "its",
    "this", "that", "these", "those", "there", "here", "which", "who", "whom",
    "whose", "what", "when", "where", "how", "not", "no", "but", "if", "then",
    "than", "so", "such", "can", "could", "will", "would", "shall", "should",
    "may", "might", "must", "do", "does", "did", "have", "has", "had", "we",
    "you", "he", "she", "they", "them", "his", "her", "their", "our", "your",
}


@dataclass
class Token:
    norm: str
    start: int  # char offset in original text
    end: int


@dataclass
class Span:
    """A matched region, in token indices and original char offsets."""
    tok_start: int
    tok_end: int  # exclusive
    char_start: int
    char_end: int
    words: int

    def to_json(self) -> dict:
        return {
            "start": self.char_start,
            "end": self.char_end,
            "words": self.words,
        }


@dataclass
class SourceMatch:
    source_id: int
    source_name: str
    source_kind: str          # "db" or "web"
    source_url: str = ""
    spans: list = field(default_factory=list)      # list[Span] in doc coords
    matched_words: int = 0

    def to_json(self) -> dict:
        return {
            "sourceId": self.source_id,
            "sourceName": self.source_name,
            "sourceKind": self.source_kind,
            "sourceUrl": self.source_url,
            "matchedWords": self.matched_words,
            "spans": [s.to_json() for s in self.spans],
        }


def normalize(text: str) -> str:
    text = text.replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"')
    text = text.replace("–", "-").replace("—", "-")
    return text.lower()


def tokenize(text: str) -> list[Token]:
    """Tokens over the *normalized* text; caller maps char offsets back if needed."""
    return [Token(m.group(0), m.start(), m.end()) for m in WORD_RE.finditer(text)]


def _is_contentful(tokens: list[Token]) -> bool:
    """A span is meaningful if at least half its words are non-stopwords."""
    content = sum(1 for t in tokens if t.norm not in STOPWORDS)
    return content >= max(2, len(tokens) // 2)


def match_document(doc_text: str, source_text: str) -> list[Span]:
    """Return maximal matching spans of doc_text vs source_text (char offsets in doc)."""
    doc_norm = normalize(doc_text)
    src_norm = normalize(source_text)
    doc_toks = tokenize(doc_norm)
    src_toks = tokenize(src_norm)
    n, m = len(doc_toks), len(src_toks)
    if n < K or m < K:
        return []

    # Index source k-grams -> list of positions.
    index: dict[tuple, list[int]] = {}
    for i in range(m - K + 1):
        gram = tuple(t.norm for t in src_toks[i:i + K])
        index.setdefault(gram, []).append(i)

    covered = [False] * n
    spans: list[Span] = []

    i = 0
    while i <= n - K:
        gram = tuple(t.norm for t in doc_toks[i:i + K])
        positions = index.get(gram)
        if not positions:
            i += 1
            continue

        # Greedily extend the longest alignment starting at any seed position.
        best_len = 0
        for j in positions:
            length = K
            while (
                i + length < n
                and j + length < m
                and doc_toks[i + length].norm == src_toks[j + length].norm
            ):
                length += 1
            if length > best_len:
                best_len = length

        span_toks = doc_toks[i:i + best_len]
        if best_len >= K and (best_len >= MIN_SPAN_WORDS or _is_contentful(span_toks)):
            spans.append(Span(
                tok_start=i,
                tok_end=i + best_len,
                char_start=doc_toks[i].start,
                char_end=doc_toks[i + best_len - 1].end,
                words=best_len,
            ))
            for k2 in range(i, i + best_len):
                covered[k2] = True
            i += best_len  # skip past consumed span
        else:
            i += 1

    return spans


def merge_char_spans(spans: list[Span]) -> list[tuple[int, int]]:
    """Union of char intervals, for overall-coverage scoring."""
    if not spans:
        return []
    ivals = sorted((s.char_start, s.char_end) for s in spans)
    merged = [list(ivals[0])]
    for start, end in ivals[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(a, b) for a, b in merged]


def overall_similarity(doc_text: str, all_spans: list[Span]) -> float:
    """Fraction of the document's word characters covered by any match."""
    if not doc_text.strip():
        return 0.0
    total_words = len(tokenize(normalize(doc_text)))
    if total_words == 0:
        return 0.0
    matched = sum(s.words for s in all_spans)
    return round(min(1.0, matched / total_words), 4)


def build_report(doc_text: str, sources: list[dict]) -> dict:
    """Run full comparison against every source.

    sources: list of {id, name, kind, url?, text}
    Returns report dict with per-source matches + overall similarity.
    """
    doc_norm = normalize(doc_text)
    total_words = len(tokenize(doc_norm))
    source_results = []
    all_spans: list[Span] = []

    for src in sources:
        spans = match_document(doc_text, src["text"])
        if spans:
            matched_words = sum(s.words for s in spans)
            all_spans.extend(spans)
            source_results.append(SourceMatch(
                source_id=src["id"],
                source_name=src["name"],
                source_kind=src["kind"],
                source_url=src.get("url", ""),
                spans=spans,
                matched_words=matched_words,
            ))

    source_results.sort(key=lambda r: r.matched_words, reverse=True)
    overall = overall_similarity(doc_text, all_spans)
    return {
        "overall": overall,
        "wordCount": total_words,
        "sources": [r.to_json() for r in source_results],
    }
