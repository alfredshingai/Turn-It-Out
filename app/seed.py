"""Seed demo data on first run: accounts, class, assignment, reference corpus,
and a plagiarized student submission that demonstrably matches the corpus."""
import os

from . import auth, db, similarity

REFERENCE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reference_corpus")

DEMO_ACCOUNTS = [
    ("instructor@demo.edu", "Prof. Dana Reyes", "instructor", "instructor123"),
    ("student@demo.edu", "Alex Chen", "student", "student123"),
]

REFERENCE_FILES = [
    "climate_urban_heat.txt",
    "quantum_computing_intro.txt",
    "social_media_mental_health.txt",
]


def ensure_seed() -> None:
    if db.query_one("SELECT id FROM users LIMIT 1"):
        return

    users = {}
    for email, name, role, password in DEMO_ACCOUNTS:
        uid = db.execute(
            "INSERT INTO users (email, name, role, password_hash, created_at) VALUES (?, ?, ?, ?, ?)",
            (email, name, role, auth.hash_password(password), db.now()),
        )
        users[role] = uid

    cid = db.execute(
        "INSERT INTO classes (name, code, instructor_id, created_at) VALUES (?, ?, ?, ?)",
        ("ENG 101: Composition", "ENG101", users["instructor"], db.now()),
    )
    db.execute(
        "INSERT INTO enrollments (class_id, student_id, created_at) VALUES (?, ?, ?)",
        (cid, users["student"], db.now()),
    )
    aid = db.execute(
        "INSERT INTO assignments (class_id, title, instructions, due_at, created_at) VALUES (?, ?, ?, ?, ?)",
        (cid, "Essay 1: Technology and Society",
         "Write a 500+ word essay on how a modern technology affects society. Cite sources.",
         "2026-12-01", db.now()),
    )

    # Reference corpus: stored as a hidden reference assignment.
    ref_aid = db.execute(
        "INSERT INTO assignments (class_id, title, instructions, is_reference, created_at) VALUES (?, ?, ?, 1, ?)",
        (cid, "[Reference corpus]", "", db.now()),
    )
    for fname in REFERENCE_FILES:
        path = os.path.join(REFERENCE_DIR, fname)
        if not os.path.isfile(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        db.execute(
            """INSERT INTO submissions
               (assignment_id, student_id, filename, ext, text, char_count, word_count,
                status, similarity, is_reference, created_at)
               VALUES (?, ?, ?, 'txt', ?, ?, ?, 'done', 0, 1, ?)""",
            (ref_aid, users["instructor"], fname, text, len(text), len(text.split()), db.now()),
        )

    # A student submission that copies from the corpus -> instant demo match.
    ref_rows = db.query(
        "SELECT text FROM submissions WHERE assignment_id = ? AND is_reference = 1",
        (ref_aid,),
    )
    original_intro = (
        "In the last twenty years, no technology has reshaped daily life more quietly than the "
        "smartphone. It sits beside us at dinner, rides with us on the train, and lights up our "
        "faces long after the house has gone dark. Yet the same device that connects us to the "
        "whole world can quietly disconnect us from the room we are sitting in. What makes this "
        "tension interesting is how ordinary it has become: nobody plans to spend three hours "
        "scrolling, just as nobody plans to let a notification interrupt a conversation, and "
        "yet by the end of most evenings the screen has quietly won. My own habits are no "
        "exception, which is partly why I wanted to examine the technology honestly rather "
        "than simply condemn it, looking at both what the smartphone gives and what it "
        "quietly takes away from the people glued to it."
    )
    copied = ref_rows[0]["text"] if ref_rows else ""
    mixed = original_intro + "\n\n" + copied
    sid = db.execute(
        """INSERT INTO submissions
           (assignment_id, student_id, filename, ext, text, char_count, word_count, status, created_at)
           VALUES (?, ?, 'essay1_alex_chen.txt', 'txt', ?, ?, ?, 'queued', ?)""",
        (aid, users["student"], mixed, len(mixed), len(mixed.split()), db.now()),
    )
    from . import scanner
    scanner.run_scan(sid, use_web=False)  # seed synchronously so demo shows a report immediately
