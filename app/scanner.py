"""Scan orchestration: compares a document against the global anonymized corpus
and (optionally) the web, plus AI-writing analysis, then persists the report."""
import json
import threading
import traceback

from . import aidetect, db, similarity, webcheck

MAX_DB_SOURCES = 300
SCAN_THREADS: dict[int, threading.Thread] = {}
_lock = threading.Lock()


def candidate_sources(doc: dict) -> list[dict]:
    """Corpus: every other document with text, most recent first, capped.
    Sources are anonymized — filename is never shown, only Document #id
    (plus a marker for seeded reference-corpus samples)."""
    rows = db.query(
        """SELECT id, filename, is_reference FROM documents
           WHERE id != ? AND char_count > 0
           ORDER BY id DESC
           LIMIT ?""",
        (doc["id"], MAX_DB_SOURCES),
    )
    return [
        {
            "id": r["id"],
            "name": f"Document #{r['id']}" + (" [sample corpus]" if r["is_reference"] else ""),
            "kind": "db",
            "url": "",
            "text": "",  # filled by caller in chunks below
        }
        for r in rows
    ]


def run_scan(document_id: int, use_web: bool = True) -> None:
    doc = db.query_one("SELECT * FROM documents WHERE id = ?", (document_id,))
    if not doc:
        return
    try:
        db.execute(
            "UPDATE documents SET status = 'processing', error = '' WHERE id = ?",
            (document_id,),
        )
        meta = candidate_sources(doc)

        sources = []
        for m in meta:
            row = db.query_one("SELECT text FROM documents WHERE id = ?", (m["id"],))
            if row and row["text"]:
                m["text"] = row["text"]
                sources.append(m)

        web_sources: list[dict] = []
        if use_web:
            try:
                web_sources = webcheck.web_check(doc["text"])
            except Exception:
                web_sources = []  # fail soft; offline scans still work

        report = similarity.build_report(doc["text"], sources + web_sources)

        ai = aidetect.analyze(doc["text"])
        ai_score = ai.get("aiScore")

        db.execute(
            """UPDATE documents
               SET status = 'done', similarity = ?, report = ?, scanned_at = ?,
                   ai_score = ?, ai_report = ?, error = ''
               WHERE id = ?""",
            (report["overall"], json.dumps(report), db.now(),
             ai_score, json.dumps(ai), document_id),
        )
    except Exception as exc:
        db.execute(
            "UPDATE documents SET status = 'error', error = ? WHERE id = ?",
            (str(exc) or traceback.format_exc()[-300:], document_id),
        )
    finally:
        with _lock:
            SCAN_THREADS.pop(document_id, None)


def start_scan(document_id: int, use_web: bool = True) -> None:
    """Kick off a scan in a background thread (idempotent per document)."""
    with _lock:
        if document_id in SCAN_THREADS and SCAN_THREADS[document_id].is_alive():
            return
        t = threading.Thread(
            target=run_scan, args=(document_id, use_web), daemon=True,
            name=f"scan-{document_id}",
        )
        SCAN_THREADS[document_id] = t
        t.start()
