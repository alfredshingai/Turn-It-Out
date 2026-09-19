"""SQLite data layer for TurnitOut. Stdlib only."""
import os
import sqlite3
import threading
import time

DB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DB_PATH = os.environ.get("TURNITOUT_DB", os.path.join(DB_DIR, "turnitout.db"))

_write_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('instructor', 'student')),
    password_hash TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS classes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    code TEXT NOT NULL UNIQUE,
    instructor_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS enrollments (
    class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at REAL NOT NULL,
    PRIMARY KEY (class_id, student_id)
);
CREATE TABLE IF NOT EXISTS assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    instructions TEXT NOT NULL DEFAULT '',
    due_at TEXT,
    is_reference INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assignment_id INTEGER NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
    student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    ext TEXT NOT NULL DEFAULT 'txt',
    text TEXT NOT NULL DEFAULT '',
    char_count INTEGER NOT NULL DEFAULT 0,
    word_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'processing', 'done', 'error')),
    similarity REAL NOT NULL DEFAULT 0,
    report TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    is_reference INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    scanned_at REAL
);
CREATE INDEX IF NOT EXISTS idx_submissions_assignment ON submissions(assignment_id);
CREATE INDEX IF NOT EXISTS idx_assignments_class ON assignments(class_id);
CREATE TABLE IF NOT EXISTS invites (
    code TEXT PRIMARY KEY,
    created_by INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    used_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at REAL NOT NULL,
    used_at REAL
);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns to databases created before the AI-detection feature."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(submissions)")}
    if "ai_score" not in cols:
        conn.execute("ALTER TABLE submissions ADD COLUMN ai_score REAL")
    if "ai_report" not in cols:
        conn.execute("ALTER TABLE submissions ADD COLUMN ai_report TEXT NOT NULL DEFAULT ''")


def connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with _write_lock:
        conn = connect()
        try:
            conn.executescript(SCHEMA)
            _migrate(conn)
            conn.commit()
        finally:
            conn.close()


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


def now() -> float:
    return time.time()


def recover_stale_scans() -> int:
    """Mark submissions stuck in processing/queued as failed (e.g. after a crash/restart)
    so users can Rescan them. Returns how many were recovered."""
    rows = query(
        "SELECT id FROM submissions WHERE status IN ('processing', 'queued')")
    for r in rows:
        execute(
            "UPDATE submissions SET status = 'error', error = ? WHERE id = ?",
            ("Scan interrupted (server restart); use Rescan to retry.", r["id"]),
        )
    return len(rows)