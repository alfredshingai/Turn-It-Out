"""TurnitOut HTTP server: routing, auth, REST API, static files. Stdlib only."""
import json
import logging
import mimetypes
import os
import ipaddress
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from email.parser import BytesParser
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import aidetect, auth, db, extract, scanner

log = logging.getLogger("turnitout")

RATE_LIMITS = {
    ("POST", "/api/login"): (5, 300),       # 5 attempts / 5 min per IP
    ("POST", "/api/register"): (30, 3600),  # generous: a whole class may share one NAT IP
}
_rate_lock = threading.Lock()
_rate_buckets: dict = {}


def _client_ip(handler) -> str:
    """Best-effort client IP: trust X-Forwarded-For only from a private proxy hop
    (a reverse proxy on the same host/private network rewrites XFF to the real
    client; a spoofed public XFF won't be trusted)."""
    xff = handler.headers.get("X-Forwarded-For", "")
    ip = handler.client_address[0]
    if xff:
        first = xff.split(",")[0].strip()
        if ipaddress.ip_address(ip).is_private:
            return first or ip
    return ip


def _rate_limit(key, limit: int, window: float) -> bool:
    """Sliding-window limiter; returns True when allowed."""
    now = time.time()
    with _rate_lock:
        bucket = _rate_buckets.setdefault(key, deque())
        while bucket and bucket[0] <= now - window:
            bucket.popleft()
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True

PUBLIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "public")
MAX_BODY = 11 * 1024 * 1024
SESSION_COOKIE = "turnitout_session"


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def parse_multipart(content_type: str, body: bytes):
    msg = BytesParser(policy=HTTP).parsebytes(
        b"Content-Type: " + content_type.encode()
        + b"\r\nMIME-Version: 1.0\r\n\r\n" + body
    )
    fields, files = {}, {}
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if name is None:
            continue
        filename = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        if filename:
            files[name] = {"filename": os.path.basename(filename), "data": payload}
        else:
            fields[name] = payload.decode("utf-8", "replace")
    return fields, files


def public_user(row: dict) -> dict:
    return {"id": row["id"], "email": row["email"], "name": row["name"], "role": row["role"]}


def require_user(ctx) -> dict:
    if not ctx["user"]:
        raise ApiError(401, "Not signed in")
    return ctx["user"]


def require_role(user: dict, role: str) -> None:
    if user["role"] != role:
        raise ApiError(403, f"Requires {role} role")


def class_for_access(class_id: int, user: dict) -> dict:
    cls = db.query_one("SELECT * FROM classes WHERE id = ?", (class_id,))
    if not cls:
        raise ApiError(404, "Class not found")
    if user["role"] == "instructor" and cls["instructor_id"] == user["id"]:
        return cls
    if user["role"] == "student":
        enrolled = db.query_one(
            "SELECT 1 AS ok FROM enrollments WHERE class_id = ? AND student_id = ?",
            (class_id, user["id"]),
        )
        if enrolled:
            return cls
    raise ApiError(403, "No access to this class")


def assignment_for_access(assignment_id: int, user: dict) -> dict:
    a = db.query_one("SELECT * FROM assignments WHERE id = ?", (assignment_id,))
    if not a:
        raise ApiError(404, "Assignment not found")
    class_for_access(a["class_id"], user)
    return a


def submission_for_access(submission_id: int, user: dict) -> dict:
    sub = db.query_one("SELECT * FROM submissions WHERE id = ?", (submission_id,))
    if not sub:
        raise ApiError(404, "Submission not found")
    a = db.query_one("SELECT * FROM assignments WHERE id = ?", (sub["assignment_id"],))
    cls = db.query_one("SELECT * FROM classes WHERE id = ?", (a["class_id"],))
    if user["role"] == "instructor" and cls["instructor_id"] == user["id"]:
        return sub
    if user["role"] == "student" and sub["student_id"] == user["id"]:
        return sub
    raise ApiError(403, "No access to this submission")


# ---------------------------------------------------------------- handlers

def _secure_cookies() -> bool:
    """Set Secure on cookies when serving HTTPS (proxy or direct)."""
    if os.environ.get("TURNITOUT_TRUST_PROXY") == "1":
        return True
    return os.environ.get("TURNITOUT_HTTPS", "0") == "1"


def _auth_cookie(token: str, max_age: int) -> str:
    parts = [f"{SESSION_COOKIE}={token}", "Path=/", "HttpOnly", "SameSite=Lax",
             f"Max-Age={max_age}"]
    if _secure_cookies():
        parts.append("Secure")
    return "; ".join(parts)


def h_login(ctx):
    body = ctx["body"]
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    # Timing-safe-ish: always run a hash comparison even when the user is unknown.
    row = db.query_one("SELECT * FROM users WHERE email = ?", (email,))
    stored = row["password_hash"] if row else "scrypt$00$" + "0" * 64
    ok = auth.verify_password(password, stored)
    if not row or not ok:
        raise ApiError(401, "Invalid email or password")
    token = auth.create_session(row["id"])
    return 200, {"user": public_user(row)}, [
        ("Set-Cookie", _auth_cookie(token, auth.SESSION_TTL))
    ]


def h_logout(ctx):
    if ctx["token"]:
        auth.delete_session(ctx["token"])
    return 200, {"ok": True}, [
        ("Set-Cookie", _auth_cookie("", 0))
    ]


def h_me(ctx):
    return 200, {"user": ctx["user"]}


def h_register(ctx):
    body = ctx["body"]
    email = (body.get("email") or "").strip().lower()
    name = (body.get("name") or "").strip()
    password = body.get("password") or ""
    role = body.get("role") or "student"
    invite_code = (body.get("inviteCode") or "").strip().upper()
    if not email or "@" not in email:
        raise ApiError(400, "Valid email required")
    if not name:
        raise ApiError(400, "Name required")
    if len(password) < 6:
        raise ApiError(400, "Password must be at least 6 characters")
    if role not in ("instructor", "student"):
        role = "student"
    if role == "instructor":
        if _has_instructor():
            invite = db.query_one(
                "SELECT * FROM invites WHERE code = ? AND used_by IS NULL", (invite_code,))
            if not invite:
                raise ApiError(403, "Instructor sign-up requires a valid invite code")
        # First instructor bootstraps the instance without an invite.
    if db.query_one("SELECT id FROM users WHERE email = ?", (email,)):
        raise ApiError(409, "An account with that email already exists")
    uid = db.execute(
        "INSERT INTO users (email, name, role, password_hash, created_at) VALUES (?, ?, ?, ?, ?)",
        (email, name, role, auth.hash_password(password), db.now()),
    )
    if role == "instructor" and _has_instructor() and invite_code:
        db.execute("UPDATE invites SET used_by = ?, used_at = ? WHERE code = ?",
                   (uid, db.now(), invite_code))
    row = db.query_one("SELECT * FROM users WHERE id = ?", (uid,))
    token = auth.create_session(uid)
    return 200, {"user": public_user(row)}, [
        ("Set-Cookie", _auth_cookie(token, auth.SESSION_TTL))
    ]


def _has_instructor() -> bool:
    return bool(db.query_one(
        "SELECT id FROM users WHERE role = 'instructor' LIMIT 1"))


def h_invite_create(ctx):
    user = require_user(ctx)
    require_role(user, "instructor")
    code = "INV-" + secrets.token_urlsafe(9)  # ~12 url-safe chars
    db.execute(
        "INSERT INTO invites (code, created_by, created_at) VALUES (?, ?, ?)",
        (code, user["id"], db.now()),
    )
    return 200, {"invite": {"code": code, "createdAt": db.now()}}


def h_invite_list(ctx):
    user = require_user(ctx)
    require_role(user, "instructor")
    rows = db.query(
        """SELECT i.code, i.created_at, i.used_at, u.name AS used_by_name
           FROM invites i LEFT JOIN users u ON u.id = i.used_by
           WHERE i.created_by = ? ORDER BY i.created_at DESC LIMIT 50""",
        (user["id"],),
    )
    return 200, {"invites": rows}


def h_classes_list(ctx):
    user = require_user(ctx)
    if user["role"] == "instructor":
        rows = db.query(
            """SELECT c.*, (SELECT COUNT(*) FROM enrollments e WHERE e.class_id = c.id) AS students,
                      (SELECT COUNT(*) FROM assignments a WHERE a.class_id = c.id AND a.is_reference = 0) AS assignments
               FROM classes c WHERE c.instructor_id = ? ORDER BY c.id DESC""",
            (user["id"],),
        )
    else:
        rows = db.query(
            """SELECT c.*, (SELECT COUNT(*) FROM assignments a WHERE a.class_id = c.id AND a.is_reference = 0) AS assignments
               FROM classes c JOIN enrollments e ON e.class_id = c.id
               WHERE e.student_id = ? ORDER BY c.id DESC""",
            (user["id"],),
        )
    return 200, {"classes": rows}


def h_classes_create(ctx):
    user = require_user(ctx)
    require_role(user, "instructor")
    name = (ctx["body"].get("name") or "").strip()
    if not name:
        raise ApiError(400, "Class name required")
    code = None
    for _ in range(20):
        candidate = _join_code(6)
        if not db.query_one("SELECT id FROM classes WHERE code = ?", (candidate,)):
            code = candidate
            break
    cid = db.execute(
        "INSERT INTO classes (name, code, instructor_id, created_at) VALUES (?, ?, ?, ?)",
        (name, code, user["id"], db.now()),
    )
    return 200, {"class": db.query_one("SELECT * FROM classes WHERE id = ?", (cid,))}


def _join_code(n: int) -> str:
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(n))


def h_classes_join(ctx):
    user = require_user(ctx)
    require_role(user, "student")
    code = (ctx["body"].get("code") or "").strip().upper()
    cls = db.query_one("SELECT * FROM classes WHERE code = ?", (code,))
    if not cls:
        raise ApiError(404, "No class with that code")
    if db.query_one(
        "SELECT 1 AS ok FROM enrollments WHERE class_id = ? AND student_id = ?",
        (cls["id"], user["id"]),
    ):
        return 200, {"class": cls, "already": True}
    db.execute(
        "INSERT INTO enrollments (class_id, student_id, created_at) VALUES (?, ?, ?)",
        (cls["id"], user["id"], db.now()),
    )
    return 200, {"class": cls}


def h_class_detail(ctx):
    user = require_user(ctx)
    cid = int(ctx["match"].group("id"))
    cls = class_for_access(cid, user)
    assignments = db.query(
        """SELECT a.*,
                  (SELECT COUNT(*) FROM submissions s WHERE s.assignment_id = a.id) AS submission_count,
                  (SELECT COUNT(*) FROM submissions s WHERE s.assignment_id = a.id
                     AND s.student_id = ? AND s.status = 'done') AS my_scanned
           FROM assignments a WHERE a.class_id = ? AND a.is_reference = 0 ORDER BY a.id DESC""",
        (user["id"], cid),
    )
    out = {"class": cls, "assignments": assignments}
    if user["role"] == "instructor":
        roster = db.query(
            """SELECT u.id, u.name, u.email,
                      (SELECT COUNT(*) FROM submissions s
                         JOIN assignments a ON a.id = s.assignment_id
                        WHERE a.class_id = ? AND s.student_id = u.id) AS submissions
               FROM enrollments e JOIN users u ON u.id = e.student_id
               WHERE e.class_id = ? ORDER BY u.name""",
            (cid, cid),
        )
        out["roster"] = roster
    return 200, out


def h_assignment_create(ctx):
    user = require_user(ctx)
    require_role(user, "instructor")
    cid = int(ctx["match"].group("id"))
    cls = class_for_access(cid, user)
    body = ctx["body"]
    title = (body.get("title") or "").strip()
    if not title:
        raise ApiError(400, "Assignment title required")
    aid = db.execute(
        "INSERT INTO assignments (class_id, title, instructions, due_at, created_at) VALUES (?, ?, ?, ?, ?)",
        (cid, title, (body.get("instructions") or "").strip(),
         (body.get("dueAt") or "").strip() or None, db.now()),
    )
    return 200, {"assignment": db.query_one("SELECT * FROM assignments WHERE id = ?", (aid,))}


def h_assignment_detail(ctx):
    user = require_user(ctx)
    aid = int(ctx["match"].group("id"))
    a = assignment_for_access(aid, user)
    subs = db.query(
        """SELECT s.id, s.student_id, s.filename, s.ext, s.status, s.similarity,
                  s.ai_score, s.char_count, s.word_count, s.created_at, s.scanned_at,
                  u.name AS student_name
           FROM submissions s JOIN users u ON u.id = s.student_id
           WHERE s.assignment_id = ? ORDER BY s.id DESC""",
        (aid,),
    )
    if user["role"] == "student":
        subs = [s for s in subs if s["student_id"] == user["id"]]
    return 200, {"assignment": a, "submissions": subs}


def h_assignment_submit(ctx):
    user = require_user(ctx)
    require_role(user, "student")
    aid = int(ctx["match"].group("id"))
    a = assignment_for_access(aid, user)
    fileinfo = ctx["files"].get("file")
    if not fileinfo or not fileinfo["data"]:
        raise ApiError(400, "Attach a file in the 'file' field")
    try:
        text = extract.extract_text(fileinfo["filename"], fileinfo["data"])
    except extract.ExtractError as exc:
        raise ApiError(400, str(exc))
    words = len(text.split())
    sid = db.execute(
        """INSERT INTO submissions
           (assignment_id, student_id, filename, ext, text, char_count, word_count,
            status, is_reference, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', 0, ?)""",
        (aid, user["id"], fileinfo["filename"], fileinfo["filename"].rsplit(".", 1)[-1].lower(),
         text, len(text), words, db.now()),
    )
    scanner.start_scan(sid, use_web=True)
    return 200, {"submission": db.query_one(
        "SELECT id, filename, status, created_at FROM submissions WHERE id = ?", (sid,))}


def h_submission_get(ctx):
    user = require_user(ctx)
    sid = int(ctx["match"].group("id"))
    sub = submission_for_access(sid, user)
    out = {k: sub[k] for k in ("id", "assignment_id", "student_id", "filename", "ext",
                               "status", "similarity", "char_count", "word_count",
                               "ai_score", "error", "created_at", "scanned_at")}
    return 200, {"submission": out}


def h_ai_status(ctx):
    require_user(ctx)
    return 200, {"ai": aidetect.provider_status()}


def h_submission_report(ctx):
    user = require_user(ctx)
    sid = int(ctx["match"].group("id"))
    sub = submission_for_access(sid, user)
    report = json.loads(sub["report"]) if sub["report"] else None
    ai_report = json.loads(sub["ai_report"]) if sub["ai_report"] else None
    return 200, {
        "submission": {
            "id": sub["id"], "filename": sub["filename"], "status": sub["status"],
            "similarity": sub["similarity"], "wordCount": sub["word_count"],
            "aiScore": sub["ai_score"], "error": sub["error"],
        },
        "text": sub["text"],
        "report": report,
        "aiReport": ai_report,
    }


def h_submission_rescan(ctx):
    user = require_user(ctx)
    sid = int(ctx["match"].group("id"))
    submission_for_access(sid, user)
    use_web = bool(ctx["body"].get("useWeb", True))
    scanner.start_scan(sid, use_web=use_web)
    return 200, {"ok": True, "status": "queued"}


def h_stats(ctx):
    user = require_user(ctx)
    if user["role"] == "instructor":
        classes = db.query("SELECT COUNT(*) AS c FROM classes WHERE instructor_id = ?", (user["id"],))[0]["c"]
        students = db.query(
            """SELECT COUNT(DISTINCT e.student_id) AS c FROM enrollments e
               JOIN classes cl ON cl.id = e.class_id WHERE cl.instructor_id = ?""",
            (user["id"],),
        )[0]["c"]
        subs = db.query(
            """SELECT COUNT(*) AS c, AVG(CASE WHEN s.status='done' THEN s.similarity END) AS avg
               FROM submissions s JOIN assignments a ON a.id = s.assignment_id
               JOIN classes cl ON cl.id = a.class_id WHERE cl.instructor_id = ?""",
            (user["id"],),
        )[0]
        flagged = db.query(
            """SELECT COUNT(*) AS c FROM submissions s
               JOIN assignments a ON a.id = s.assignment_id
               JOIN classes cl ON cl.id = a.class_id
               WHERE cl.instructor_id = ? AND s.status = 'done' AND s.similarity >= 0.25""",
            (user["id"],),
        )[0]["c"]
        ai_flagged = db.query(
            """SELECT COUNT(*) AS c FROM submissions s
               JOIN assignments a ON a.id = s.assignment_id
               JOIN classes cl ON cl.id = a.class_id
               WHERE cl.instructor_id = ? AND s.status = 'done'
                 AND s.ai_score IS NOT NULL AND s.ai_score >= 0.5""",
            (user["id"],),
        )[0]["c"]
    else:
        classes = db.query(
            "SELECT COUNT(*) AS c FROM enrollments WHERE student_id = ?", (user["id"],)
        )[0]["c"]
        students = 0
        subs = db.query(
            "SELECT COUNT(*) AS c, AVG(CASE WHEN status='done' THEN similarity END) AS avg FROM submissions WHERE student_id = ?",
            (user["id"],),
        )[0]
        flagged = db.query(
            "SELECT COUNT(*) AS c FROM submissions WHERE student_id = ? AND status='done' AND similarity >= 0.25",
            (user["id"],),
        )[0]["c"]
        ai_flagged = db.query(
            """SELECT COUNT(*) AS c FROM submissions WHERE student_id = ?
               AND status='done' AND ai_score IS NOT NULL AND ai_score >= 0.5""",
            (user["id"],),
        )[0]["c"]
    return 200, {
        "classes": classes,
        "students": students,
        "submissions": subs["c"],
        "avgSimilarity": round(subs["avg"] or 0, 4),
        "flagged": flagged,
        "aiFlagged": ai_flagged,
    }


def h_health(ctx):
    db.query_one("SELECT 1 AS ok")
    return 200, {"ok": True, "time": db.now(), "aiMode": aidetect.provider_status()["mode"]}


ROUTES = [
    ("POST", r"^/api/login$", h_login),
    ("POST", r"^/api/logout$", h_logout),
    ("GET", r"^/api/me$", h_me),
    ("POST", r"^/api/register$", h_register),
    ("GET", r"^/api/ai/status$", h_ai_status),
    ("POST", r"^/api/invites$", h_invite_create),
    ("GET", r"^/api/invites$", h_invite_list),
    ("GET", r"^/api/health$", h_health),
    ("GET", r"^/api/ai/status$", h_ai_status),
    ("GET", r"^/api/stats$", h_stats),
    ("GET", r"^/api/classes$", h_classes_list),
    ("POST", r"^/api/classes$", h_classes_create),
    ("POST", r"^/api/classes/join$", h_classes_join),
    ("GET", r"^/api/classes/(?P<id>\d+)$", h_class_detail),
    ("POST", r"^/api/classes/(?P<id>\d+)/assignments$", h_assignment_create),
    ("GET", r"^/api/assignments/(?P<id>\d+)$", h_assignment_detail),
    ("POST", r"^/api/assignments/(?P<id>\d+)/submit$", h_assignment_submit),
    ("GET", r"^/api/submissions/(?P<id>\d+)$", h_submission_get),
    ("GET", r"^/api/submissions/(?P<id>\d+)/report$", h_submission_report),
    ("POST", r"^/api/submissions/(?P<id>\d+)/rescan$", h_submission_rescan),
]


class Handler(BaseHTTPRequestHandler):
    server_version = "TurnitOut/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass  # keep console quiet

    # -- helpers ------------------------------------------------------
    def _cookies(self) -> dict:
        out = {}
        raw = self.headers.get("Cookie", "")
        for part in raw.split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                out[k.strip()] = v.strip()
        return out

    def _send_json(self, status: int, obj, extra_headers=None):
        payload = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra_headers or []):
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(payload)

    def _static(self, path: str):
        if path == "/":
            path = "/index.html"
        rel = path.lstrip("/")
        full = os.path.normpath(os.path.join(PUBLIC_DIR, rel))
        if not full.startswith(os.path.normpath(PUBLIC_DIR)) or not os.path.isfile(full):
            # SPA fallback
            full = os.path.join(PUBLIC_DIR, "index.html")
            if not os.path.isfile(full):
                self._send_json(404, {"error": "Not found"})
                return
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(data)

    # -- dispatch -----------------------------------------------------
    def _dispatch(self, method: str):
        path = self.path.split("?", 1)[0]
        if not path.startswith("/api"):
            if method == "GET":
                return self._static(path)
            self._send_json(404, {"error": "Not found"})
            return

        # Rate limit sensitive endpoints per client IP.
        lim = RATE_LIMITS.get((method, path))
        if lim:
            ip = _client_ip(self)
            if not _rate_limit((ip, method, path), lim[0], lim[1]):
                log.warning("RATE LIMIT %s %s from %s", method, path, ip)
                return self._send_json(429, {"error": "Too many requests — try again later."})

        body: dict = {}
        files: dict = {}
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            if length > MAX_BODY:
                return self._send_json(413, {"error": "Request too large"})
            raw = self.rfile.read(length)
            ctype = self.headers.get("Content-Type", "")
            if ctype.startswith("multipart/form-data"):
                fields, files = parse_multipart(ctype, raw)
                body = fields
            else:
                try:
                    body = json.loads(raw.decode("utf-8") or "{}")
                except json.JSONDecodeError:
                    return self._send_json(400, {"error": "Invalid JSON body"})

        cookies = self._cookies()
        token = cookies.get(SESSION_COOKIE, "")
        user = auth.user_for_token(token)

        for m_method, pattern, handler in ROUTES:
            if m_method != method:
                continue
            m = re.match(pattern, path)
            if not m:
                continue
            ctx = {"user": user, "token": token, "match": m,
                   "body": body, "files": files, "handler": self}
            try:
                result = handler(ctx)
                status, obj = result[0], result[1]
                extra = result[2] if len(result) > 2 else None
                log.info("%s %s -> %s", method, path, status)
                self._send_json(status, obj, extra)
            except ApiError as exc:
                log.info("%s %s -> %s (%s)", method, path, exc.status, exc.message)
                self._send_json(exc.status, {"error": exc.message})
            except Exception:
                log.exception("%s %s failed", method, path)
                self._send_json(500, {"error": "Internal server error"})
            return

        self._send_json(404, {"error": "No such endpoint"})

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")


def serve(host: str = "127.0.0.1", port: int = 8333):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    db.init_db()
    recovered = db.recover_stale_scans()
    if recovered:
        log.info("Recovered %d interrupted scan(s); they can be rescanned.", recovered)
    if os.environ.get("TURNITOUT_DEMO", "1") == "1":
        from . import seed
        seed.ensure_seed()
    else:
        log.info("Demo seeding disabled (TURNITOUT_DEMO=0). First instructor registration "
                 "bootstraps the instance.")
    auth.purge_expired_sessions()
    httpd = ThreadingHTTPServer((host, port), Handler)
    log.info("TurnitOut listening on http://%s:%s", host, port)
    return httpd
