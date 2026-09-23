"""TurnitOut HTTP server: routing, REST API, static files. Stdlib only.

Anonymous model: no accounts. Scans create documents addressed by unguessable
tokens; the token is the capability. Rate limits protect the free service.
"""
import ipaddress
import json
import logging
import mimetypes
import os
import re
import threading
import time
from collections import deque
from email.parser import BytesParser
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import aidetect, db, extract, humanize, scanner

log = logging.getLogger("turnitout")

PUBLIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "public")
MAX_BODY = 11 * 1024 * 1024

RATE_LIMITS = {
    ("POST", "/api/scan"): (10, 3600),  # 10 scans/hour per IP — free, not abusable
    ("POST", "/api/humanize"): (20, 3600),  # enhancer is cheap + local
}
_rate_lock = threading.Lock()
_rate_buckets: dict = {}


def _client_ip(handler) -> str:
    xff = handler.headers.get("X-Forwarded-For", "")
    ip = handler.client_address[0]
    if xff:
        first = xff.split(",")[0].strip()
        if ipaddress.ip_address(ip).is_private:
            return first or ip
    return ip


def _rate_limit(key, limit: int, window: float) -> bool:
    now = time.time()
    with _rate_lock:
        bucket = _rate_buckets.setdefault(key, deque())
        while bucket and bucket[0] <= now - window:
            bucket.popleft()
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True


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


def doc_for_token(token: str) -> dict:
    doc = db.query_one("SELECT * FROM documents WHERE token = ?", (token,))
    if not doc:
        raise ApiError(404, "Report not found — check the link")
    return doc


# ---------------------------------------------------------------- handlers

def h_scan(ctx):
    """Create a document from pasted text or an uploaded file and start a scan."""
    text = ""
    filename, ext = "pasted-text", "txt"
    if ctx["files"].get("file"):
        f = ctx["files"]["file"]
        if not f["data"]:
            raise ApiError(400, "Empty file")
        try:
            text = extract.extract_text(f["filename"], f["data"])
        except extract.ExtractError as exc:
            raise ApiError(400, str(exc))
        filename, ext = f["filename"], f["filename"].rsplit(".", 1)[-1].lower()
    else:
        text = (ctx["body"].get("text") or "").strip()
        if not text:
            raise ApiError(400, "Paste text or upload a file")
    words = len(text.split())
    if words < 20:
        raise ApiError(400, "Need at least 20 words to run a meaningful check")
    if words > 30000:
        raise ApiError(400, "Document too long (30,000 word max)")

    in_corpus = 1 if (ctx["body"].get("inCorpus", "1") in (1, "1", "true", True)) else 0
    token = db.new_token()
    did = db.execute(
        """INSERT INTO documents
           (token, filename, ext, text, char_count, word_count, in_corpus, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?)""",
        (token, filename, ext, text, len(text), words, in_corpus, db.now()),
    )
    scanner.start_scan(did, use_web=True)
    return 200, {"token": token, "status": "queued"}


def h_status(ctx):
    doc = doc_for_token(ctx["match"].group("token"))
    return 200, {"status": doc["status"], "similarity": doc["similarity"],
                 "aiScore": doc["ai_score"], "error": doc["error"]}


def h_report(ctx):
    doc = doc_for_token(ctx["match"].group("token"))
    report = json.loads(doc["report"]) if doc["report"] else None
    ai_report = json.loads(doc["ai_report"]) if doc["ai_report"] else None
    return 200, {
        "document": {
            "token": doc["token"], "filename": doc["filename"], "status": doc["status"],
            "similarity": doc["similarity"], "aiScore": doc["ai_score"],
            "wordCount": doc["word_count"], "inCorpus": bool(doc["in_corpus"]),
            "error": doc["error"],
            "createdAt": doc["created_at"], "scannedAt": doc["scanned_at"],
        },
        "text": doc["text"],
        "report": report,
        "aiReport": ai_report,
    }


def h_rescan(ctx):
    doc = doc_for_token(ctx["match"].group("token"))
    use_web = str(ctx["body"].get("useWeb", "1")) in ("1", "true", True)
    scanner.start_scan(doc["id"], use_web=use_web)
    return 200, {"ok": True, "status": "queued"}


def h_recent(ctx):
    """Anonymized public feed: proves the tool is alive, leaks nothing."""
    rows = db.query(
        """SELECT word_count, ext, status, similarity, ai_score, created_at
           FROM documents WHERE status = 'done' ORDER BY id DESC LIMIT 12""")
    total = db.query_one("SELECT COUNT(*) AS c FROM documents WHERE status = 'done'")["c"]
    return 200, {
        "totalScans": total,
        "recent": [
            {
                "words": r["word_count"],
                "kind": r["ext"],
                "similarity": r["similarity"],
                "aiScore": r["ai_score"],
            }
            for r in rows
        ],
    }


def h_health(ctx):
    db.query_one("SELECT 1 AS ok")
    return 200, {"ok": True, "time": db.now(), "aiMode": aidetect.provider_status()["mode"],
                 "humanizeModes": list(humanize.MODES)}


def h_humanize(ctx):
    """Writing enhancer for the author's own draft (not a bypass tool)."""
    text = (ctx["body"].get("text") or "").strip()
    if not text:
        raise ApiError(400, "Paste text to enhance")
    mode = str(ctx["body"].get("mode") or "clarity").lower()
    if mode not in humanize.MODES:
        raise ApiError(400, f"Unknown mode — choose one of {', '.join(humanize.MODES)}")
    words = len(text.split())
    if words < 5:
        raise ApiError(400, "Need at least 5 words to enhance")
    if words > 30000:
        raise ApiError(400, "Document too long (30,000 word max)")
    use_lm = str(ctx["body"].get("useLmStudio", "1")) in ("1", "true", True)
    try:
        result = humanize.enhance(text, mode=mode, use_lmstudio=use_lm)
    except ValueError as exc:
        raise ApiError(400, str(exc))
    return 200, {
        "original": text,
        "enhanced": result["enhanced"],
        "edits": result["edits"],
        "editCount": result["editCount"],
        "provider": result["provider"],
        "mode": result["mode"],
        "stats": result["stats"],
        "note": result["note"],
    }


ROUTES = [
    ("POST", r"^/api/scan$", h_scan),
    ("GET", r"^/api/scan/(?P<token>[A-Za-z0-9_-]+)$", h_status),
    ("GET", r"^/api/report/(?P<token>[A-Za-z0-9_-]+)$", h_report),
    ("POST", r"^/api/report/(?P<token>[A-Za-z0-9_-]+)/rescan$", h_rescan),
    ("GET", r"^/api/recent$", h_recent),
    ("GET", r"^/api/health$", h_health),
    ("POST", r"^/api/humanize$", h_humanize),
]


class Handler(BaseHTTPRequestHandler):
    server_version = "TurnitOut/2.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    # -- helpers ------------------------------------------------------
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

        # Rate limit abusive endpoints per client IP.
        lim = RATE_LIMITS.get((method, path))
        if lim:
            ip = _client_ip(self)
            if not _rate_limit((ip, method, path), lim[0], lim[1]):
                log.warning("RATE LIMIT %s %s from %s", method, path, ip)
                return self._send_json(429, {"error": "Too many scans — try again later."})

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

        for m_method, pattern, handler in ROUTES:
            if m_method != method:
                continue
            m = re.match(pattern, path)
            if not m:
                continue
            ctx = {"match": m, "body": body, "files": files}
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
        demo_token = seed.ensure_seed()
        if demo_token:
            log.info("Demo document ready: /#/%s", demo_token)
    httpd = ThreadingHTTPServer((host, port), Handler)
    log.info("TurnitOut listening on http://%s:%s", host, port)
    return httpd
