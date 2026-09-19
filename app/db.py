"""SQLite data layer for TurnitOut. Stdlib only.

Access model: no accounts. A scan creates a *document* addressed by an
unguessable random token (the capability): whoever holds the token can read
the report. Documents join the global anonymized corpus so every scan makes
the tool smarter for everyone.
"""
import os
import secrets
import sqlite3
import threading
import time

DB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DB_PATH = os.environ.get("TURNITOUT_DB", os.path.join(DB_DIR, "turnitout.db"))

_write_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token TEXT NOT NULL UNIQUE,          -- unguessable capability: /report/<token>
    filename TEXT NOT NULL DEFAULT 'pasted-text',
    ext TEXT NOT NULL DEFAULT 'txt',
    text TEXT NOT NULL DEFAULT '',
    char_count INTEGER NOT NULL DEFAULT 0,
    word_count INTEGER NOT NULL DEFAULT 0,
    in_corpus INTEGER NOT NULL DEFAULT 1, -- global anonymized corpus membership
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'processing', 'done', 'error')),
    similarity REAL NOT NULL DEFAULT 0,
    report TEXT NOT NULL DEFAULT '',
    ai_score REAL,
    ai_report TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    is_reference INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    scanned_at REAL
);
CREATE INDEX IF NOT EXISTS idx_documents_token ON documents(token);
CREATE INDEX IF NOT EXISTS idx_documents_created ON documents(created_at);
"""


def connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Fresh schema for the anonymous model. Retire legacy classroom tables
    if present (data loss of old classroom DBs is intentional in this pivot)."""
    legacy = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    } & {"users", "sessions", "classes", "enrollments", "assignments", "submissions", "invites"}
    for t in legacy:
        conn.execute(f"DROP TABLE IF EXISTS {t}")


def init_db() -> None:
    with _write_lock:
        conn = connect()
        try:
            _migrate(conn)
            conn.executescript(SCHEMA)
            conn.commit()
        finally:
            conn.close()


def new_token() -> str:
    # 128 bits of entropy, URL-safe: the token IS the access control.
    return secrets.token_urlsafe(16)


def query(sql: str, params: tuple = ()) -> list[dict]:
    conn = connect()
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def query_one(sql: str, params: tuple = ()) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: tuple = ()) -> int:
    """Run a write statement; returns lastrowid."""
    with _write_lock:
        conn = connect()
        try:
            cur = conn.execute(sql, params)
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()


def recover_stale_scans() -> int:
    """Mark scans stuck in processing/queued as failed (e.g. after a crash/restart)
    so users can Rescan them. Returns how many were recovered."""
    rows = query("SELECT id FROM documents WHERE status IN ('processing', 'queued')")
    for r in rows:
        execute(
            "UPDATE documents SET status = 'error', error = ? WHERE id = ?",
            ("Scan interrupted (server restart); use Rescan to retry.", r["id"]),
        )
    return len(rows)


def now() -> float:
    return time.time()
