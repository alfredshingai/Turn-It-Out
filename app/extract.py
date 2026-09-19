"""Extract plain text from uploaded files.

- .txt / .md: native
- .docx: zipfile + XML parsing (stdlib)
- .pdf: best-effort deflate stream scraping (stdlib); may fail on some PDFs.
"""
import io
import re
import zipfile
import zlib
from xml.etree import ElementTree

MAX_BYTES = 10 * 1024 * 1024  # 10 MB

SUPPORTED_EXTS = {".txt", ".md", ".docx", ".pdf"}


class ExtractError(ValueError):
    pass


def _docx_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        xml = zf.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs = []
    for para in root.iter(f"{ns}p"):
        runs = [t.text or "" for t in para.iter(f"{ns}t")]
        paragraphs.append("".join(runs))
    return "\n".join(paragraphs)


def _pdf_text(data: bytes) -> str:
    """Scrape text from FlateDecode content streams. Best-effort."""
    text_parts: list[str] = []

    # Uncompress every FlateDecode stream we can find; keep uncompressed streams too.
    chunks: list[str] = []
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", data, re.DOTALL):
        raw = m.group(1)
        try:
            chunks.append(zlib.decompress(raw).decode("latin-1", "ignore"))
        except zlib.error:
            # Uncompressed stream: only useful if it looks like a content stream.
            try:
                text_candidate = raw.decode("latin-1", "ignore")
            except Exception:
                continue
            if "BT" in text_candidate and "Tj" in text_candidate or "TJ" in text_candidate:
                chunks.append(text_candidate)

    for chunk in chunks:
        # Text inside BT...ET blocks using Tj / TJ operators.
        for block in re.finditer(r"BT(.*?)ET", chunk, re.DOTALL):
            body = block.group(1)
            # TJ arrays: [(A) -2 (BC)] TJ  — concatenate the string pieces.
            for arr in re.finditer(r"\[(.*?)\]\s*TJ", body, re.DOTALL):
                pieces = re.findall(r"\((?:\\.|[^\\()])*\)", arr.group(1))
                s = "".join(p[1:-1] for p in pieces)
                s = s.replace("\\(", "(").replace("\\)", ")").replace("\\\\", "\\")
                if s.strip():
                    text_parts.append(s)
            # Single Tj strings.
            for tj in re.finditer(r"\(((?:\\.|[^\\()])*)\)\s*Tj", body):
                s = tj.group(1)
                s = s.replace("\\(", "(").replace("\\)", ")").replace("\\\\", "\\")
                if s.strip():
                    text_parts.append(s)

    text = " ".join(text_parts)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_text(filename: str, data: bytes) -> str:
    if len(data) > MAX_BYTES:
        raise ExtractError("File exceeds 10 MB limit")
    lower = filename.lower()
    if lower.endswith(".txt") or lower.endswith(".md"):
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return data.decode("latin-1")
    if lower.endswith(".docx"):
        try:
            text = _docx_text(data)
        except Exception as exc:
            raise ExtractError(f"Could not parse .docx: {exc}") from exc
        if not text.strip():
            raise ExtractError("No text found in .docx (may be empty or image-only)")
        return text
    if lower.endswith(".pdf"):
        text = _pdf_text(data)
        if len(text) < 40:
            raise ExtractError(
                "Could not extract text from this PDF (scanned/image PDFs are not supported)"
            )
        return text
    raise ExtractError(
        f"Unsupported file type. Supported: {', '.join(sorted(SUPPORTED_EXTS))}"
    )
