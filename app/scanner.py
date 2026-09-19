"""Scan orchestration: compares a submission against the DB corpus and (optionally)
the web, then persists a report. Runs scans in background daemon threads."""
import json
import threading
import traceback

from . import aidetect, db, similarity, webcheck

MAX_DB_SOURCES = 300
SCAN_THREADS: dict[int, threading.Thread] = {}
_lock = threading.Lock()


def candidate_sources(submission: dict) -> list[dict]:
    """Corpus for comparison: all other submissions with text (same-assignment
    first), capped for performance."""
    rows = db.query(
        """SELECT id, filename, assignment_id, text, is_reference FROM submissions
           WHERE id != ? AND char_count > 0
           ORDER BY (assignment_id = ?) DESC, is_reference DESC, id DESC
           LIMIT ?""",
        (submission["id"], submission["assignment_id"], MAX_DB_SOURCES),
    )
    return [
        {
            "id": r["id"],
            "name": f"Submission #{r['id']}: {r['filename']}"
                    + (" [reference corpus]" if r["is_reference"] else ""),
            "kind": "db",
            "url": "",
            "text": r["text"],
        }
        for r in rows
    ]


def run_scan(submission_id: int, use_web: bool = True) -> None:
    sub = db.query_one("SELECT * FROM submissions WHERE id = ?", (submission_id,))
    if not sub:
        return
    try:
        db.execute(
            "UPDATE submissions SET status = 'processing', error = '' WHERE id = ?",
            (submission_id,),
        )
        sources = candidate_sources(sub)

        web_sources: list[dict] = []
        if use_web:
            try:
                web_sources = webcheck.web_check(sub["text"])
            except Exception:
                web_sources = []  # fail soft; offline scans still work

        report = similarity.build_report(sub["text"], sources + web_sources)

        # AI-writing analysis (GPTZero if configured, else local heuristics).
        ai = aidetect.analyze(sub["text"])
        ai_score = ai.get("aiScore")

        db.execute(
            """UPDATE submissions
               SET status = 'done', similarity = ?, report = ?, scanned_at = ?,
                   ai_score = ?, ai_report = ?, error = ''
               WHERE id = ?""",
            (report["overall"], json.dumps(report), db.now(),
             ai_score, json.dumps(ai), submission_id),
        )
    except Exception as exc:  # keep failures visible in the UI
        db.execute(
            "UPDATE submissions SET status = 'error', error = ? WHERE id = ?",
            (str(exc) or traceback.format_exc()[-300:], submission_id),
        )
    finally:
        with _lock:
            SCAN_THREADS.pop(submission_id, None)


def start_scan(submission_id: int, use_web: bool = True) -> None:
    """Kick off a scan in a background thread (idempotent per submission)."""
    with _lock:
        if submission_id in SCAN_THREADS and SCAN_THREADS[submission_id].is_alive():
            return
        t = threading.Thread(
            target=run_scan, args=(submission_id, use_web), daemon=True, name=f"scan-{submission_id}"
        )
        SCAN_THREADS[submission_id] = t
        t.start()
