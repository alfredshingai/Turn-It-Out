"""Web similarity check: search distinctive phrases via DuckDuckGo HTML,
fetch nothing else, and score overlap against returned snippets.

Designed to fail soft: any network error returns empty results, so scans work
offline (DB-only mode) without errors.
"""
import html
import re
import urllib.parse
import urllib.request

from . import similarity

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TurnitOut/1.0"
TIMEOUT = 8
MIN_SENTENCE_WORDS = 8   # only search sentences long enough to be distinctive
MAX_QUERIES = 12
DDG_URL = "https://html.duckduckgo.com/html/?q={}"


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.replace("\n", " "))
    return [p.strip() for p in parts if p.strip()]


def _search_phrase(phrase: str) -> list[dict]:
    """Return [{title, url, snippet}] for an exact-ish phrase query."""
    q = urllib.parse.quote_plus(f'"{phrase}"')
    req = urllib.request.Request(DDG_URL.format(q), headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read().decode("utf-8", "ignore")
    except Exception:
        return []

    results: list[dict] = []
    # html.duckduckgo.com results: <a rel="nofollow" class="result__a" href="...">
    for m in re.finditer(
        r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>(.*?)(?=<a[^>]*class="result__a"|$)',
        body,
        re.DOTALL,
    ):
        url = html.unescape(m.group(1))
        title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        title = html.unescape(title)
        # Snippet lives in the following result__snippet block.
        snip = re.search(r'class="result__snippet"[^>]*>(.*?)</(?:a|div)>', m.group(3), re.DOTALL)
        snippet = ""
        if snip:
            snippet = html.unescape(re.sub(r"<[^>]+>", "", snip.group(1))).strip()
        # DDG wraps urls like //duckduckgo.com/l/?uddg=<encoded>&rut=...
        if "uddg=" in url:
            try:
                inner = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                url = inner.get("uddg", [url])[0]
            except Exception:
                pass
        if url.startswith("http") and title:
            results.append({"title": title, "url": url, "snippet": snippet})
        if len(results) >= 5:
            break
    return results


def web_check(text: str) -> list[dict]:
    """Check a document against the web.

    Returns list of {name, kind:'web', url, text} pseudo-sources for the
    similarity engine, one per web page whose snippet overlaps the doc.
    """
    norm = similarity.normalize(text)
    words_total = len(similarity.tokenize(norm))
    if words_total < MIN_SENTENCE_WORDS:
        return []

    sentences = [s for s in _sentences(text) if len(s.split()) >= MIN_SENTENCE_WORDS]
    # Spread queries across the document rather than only its start.
    if len(sentences) > MAX_QUERIES:
        step = len(sentences) / MAX_QUERIES
        sentences = [sentences[int(i * step)] for i in range(MAX_QUERIES)]

    # phrase -> merged page info
    pages: dict[str, dict] = {}
    for sent in sentences:
        phrase = " ".join(sent.split()[:14])
        for res in _search_phrase(phrase):
            key = res["url"]
            if key in pages:
                pages[key]["text"] += " " + res["snippet"]
            else:
                pages[key] = {
                    "id": key,
                    "name": res["title"][:120],
                    "kind": "web",
                    "url": res["url"],
                    "text": res["snippet"],
                }

    # Keep only pages whose snippet text actually matches part of the doc.
    results = []
    for page in pages.values():
        spans = similarity.match_document(text, page["text"])
        if spans:
            results.append(page)
    return results
