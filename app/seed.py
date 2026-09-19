"""First-run demo data: a sample corpus plus one document with copied passages,
so the very first visitor can open a finished report immediately."""
import os

from . import db, similarity

REFERENCE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reference_corpus")

REFERENCE_FILES = [
    "climate_urban_heat.txt",
    "quantum_computing_intro.txt",
    "social_media_mental_health.txt",
]


def ensure_seed() -> str | None:
    if db.query_one("SELECT id FROM documents LIMIT 1"):
        return None

    # Reference corpus: hidden sample documents every scan can match against.
    ref_texts = []
    ref_aid = 0
    for fname in REFERENCE_FILES:
        path = os.path.join(REFERENCE_DIR, fname)
        if not os.path.isfile(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        ref_texts.append(text)
        ref_aid = db.execute(
            """INSERT INTO documents
               (token, filename, ext, text, char_count, word_count, in_corpus,
                status, is_reference, created_at)
               VALUES (?, ?, 'txt', ?, ?, ?, 1, 'done', 1, ?)""",
            (db.new_token(), fname, text, len(text), len(text.split()), db.now()),
        )

    # Demo document: original intro + the full first corpus text -> instant match.
    original_intro = (
        "In the last twenty years, no technology has reshaped daily life more quietly than "
        "the smartphone. It sits beside us at dinner, rides with us on the train, and lights "
        "up our faces long after the house has gone dark. Yet the same device that connects "
        "us to the whole world can quietly disconnect us from the room we are sitting in. "
        "What makes this tension interesting is how ordinary it has become: nobody plans to "
        "spend three hours scrolling, just as nobody plans to let a notification interrupt a "
        "conversation, and yet by the end of most evenings the screen has quietly won. My own "
        "habits are no exception, which is partly why I wanted to examine the technology "
        "honestly rather than simply condemn it, looking at both what the smartphone gives "
        "and what it quietly takes away from the people glued to it."
    )
    copied = ref_texts[0] if ref_texts else ""
    mixed = original_intro + "\n\n" + copied
    token = db.new_token()
    sid = db.execute(
        """INSERT INTO documents
           (token, filename, ext, text, char_count, word_count, in_corpus, status, created_at)
           VALUES (?, 'demo-essay.txt', 'txt', ?, ?, ?, 1, 'queued', ?)""",
        (token, mixed, len(mixed), len(mixed.split()), db.now()),
    )
    from . import scanner
    scanner.run_scan(sid, use_web=False)  # synchronous so the demo report is ready
    return token
