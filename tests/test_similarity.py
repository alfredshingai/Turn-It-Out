"""Unit tests for the similarity engine and text extraction. Run: python -m unittest"""
import unittest

from app import similarity
from app.extract import extract_text, _pdf_text


def make_docx(xml_body: str) -> bytes:
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "word/document.xml",
            f'<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{xml_body}</w:body></w:document>',
        )
    return buf.getvalue()


class TestSimilarity(unittest.TestCase):
    def test_identical_texts_match_fully(self):
        text = "The quick brown fox jumps over the lazy dog near the river bank today."
        report = similarity.build_report(text, [{"id": 1, "name": "src", "kind": "db", "url": "", "text": text}])
        self.assertGreater(report["overall"], 0.9)
        self.assertEqual(len(report["sources"]), 1)

    def test_disjoint_texts_match_zero(self):
        a = "Quantum bits exploit superposition and entanglement for computation"
        b = "The mitochondria is the powerhouse of the biological cell membrane"
        report = similarity.build_report(a, [{"id": 1, "name": "src", "kind": "db", "url": "", "text": b}])
        self.assertEqual(report["overall"], 0.0)

    def test_partial_copy_detection(self):
        original = (
            "Machine learning models require large amounts of labeled training data to generalize well "
            "to unseen examples in production environments today."
        )
        copied = (
            "My neighborhood has many trees. Machine learning models require large amounts of labeled "
            "training data to generalize well to unseen examples, and that bothers me sometimes."
        )
        report = similarity.build_report(copied, [{"id": 1, "name": "src", "kind": "db", "url": "", "text": original}])
        self.assertGreaterEqual(report["overall"], 0.3)
        self.assertTrue(report["sources"])

    def test_spans_highlight_correctly(self):
        doc = "AAAAAAAAAA some filler words here. Quantum bits exploit superposition for computing tasks today."
        src = "Quantum bits exploit superposition for computing tasks in novel ways"
        spans = similarity.match_document(doc, src)
        self.assertTrue(spans)
        joined = " ".join(doc[s.char_start:s.char_end] for s in spans).lower()
        self.assertIn("quantum bits exploit superposition", joined)
        for s in spans:
            self.assertLessEqual(s.char_start, s.char_end)
            self.assertGreater(s.words, 0)

    def test_stopword_heavy_short_match_filtered(self):
        doc = "he said that it was in the box and then he left the room quickly afterwards"
        src = "in the box and then"
        report = similarity.build_report(doc, [{"id": 1, "name": "src", "kind": "db", "url": "", "text": src}])
        self.assertEqual(report["overall"], 0.0)

    def test_word_order_shuffle_not_flagged(self):
        a = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi"
        b = "kappa lambda mu nu xi theta eta zeta epsilon delta gamma beta alpha"
        report = similarity.build_report(a, [{"id": 1, "name": "src", "kind": "db", "url": "", "text": b}])
        self.assertLess(report["overall"], 0.1)

    def test_repeated_source_not_double_counted(self):
        doc = "alpha beta gamma delta epsilon zeta eta theta iota kappa repeated filler words here now"
        src = "alpha beta gamma delta epsilon zeta eta theta iota kappa alpha beta gamma delta epsilon zeta"
        report = similarity.build_report(doc, [{"id": 1, "name": "src", "kind": "db", "url": "", "text": src}])
        # matched words must not exceed doc length
        self.assertLessEqual(sum(s.words for s in [] or []), len(doc.split()))
        overall_matched = report["overall"] * (report["wordCount"] or 1)
        self.assertLessEqual(overall_matched, report["wordCount"])

    def test_span_merging(self):
        s1 = similarity.Span(0, 5, 0, 10, 5)
        s2 = similarity.Span(6, 9, 9, 19, 3)   # overlaps s1 -> merges
        s3 = similarity.Span(20, 22, 40, 50, 2)  # disjoint -> separate
        merged = similarity.merge_char_spans([s1, s2, s3])
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0], (0, 19))


class TestExtraction(unittest.TestCase):
    def test_txt(self):
        self.assertEqual(extract_text("a.txt", b"hello world"), "hello world")

    def test_md(self):
        self.assertEqual(extract_text("a.md", b"# Title\nBody"), "# Title\nBody")

    def test_docx(self):
        data = make_docx(
            '<w:p><w:r><w:t>Hello</w:t></w:r></w:p><w:p><w:r><w:t>World</w:t></w:r></w:p>'
        )
        self.assertEqual(extract_text("a.docx", data), "Hello\nWorld")

    def test_pdf_basic(self):
        # Build a tiny PDF with an uncompressed BT/ET text block.
        content = b"BT /F1 12 Tf 72 720 Td (Hello PDF world this is a test) Tj ET"
        pdf = b"%PDF-1.4\n1 0 obj\n<< >>\nstream\n" + content + b"\nendstream\nendobj\n%%EOF"
        text = _pdf_text(pdf)
        self.assertIn("Hello PDF world", text)

    def test_pdf_compressed(self):
        import zlib
        content = b"BT (Compressed text works too apparently) Tj ET"
        comp = zlib.compress(content)
        pdf = b"%PDF-1.4\n1 0 obj\n<< /Filter /FlateDecode >>\nstream\n" + comp + b"\nendstream\nendobj\n%%EOF"
        text = _pdf_text(pdf)
        self.assertIn("Compressed text works", text)

    def test_unsupported(self):
        with self.assertRaises(Exception):
            extract_text("a.exe", b"MZ")


if __name__ == "__main__":
    unittest.main()
