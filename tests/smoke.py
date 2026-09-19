"""End-to-end smoke test for the anonymous model: boots the server and drives
scan → status → report → rescan over real HTTP.

Run: python tests/smoke.py
"""
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["TURNITOUT_DB"] = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "smoke_test.db")  # never touch real data


def http(method: str, url: str, body=None, raw=None, ctype: str = ""):
    data = None
    headers = {}
    if raw is not None:
        data = raw
        headers["Content-Type"] = ctype
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            text = resp.read().decode()
            try:
                return resp.status, json.loads(text or "{}")
            except json.JSONDecodeError:
                return resp.status, text
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def multipart(files: dict, fields: dict) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = []
    for name, val in fields.items():
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{val}\r\n".encode())
    for name, (filename, content) in files.items():
        parts.append(
            (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; "
             f"filename=\"{filename}\"\r\nContent-Type: application/octet-stream\r\n\r\n").encode()
            + content + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def wait_done(base, token, tries=240) -> dict:
    for _ in range(tries):
        _, data = http("GET", f"{base}/api/scan/{token}")
        if data.get("status") in ("done", "error"):
            return data
        time.sleep(0.25)
    raise AssertionError("document never finished scanning")


ESSAY = (
    "This essay examines how quantum bits, or qubits, exploit superposition and entanglement. "
    "A qubit can exist in a combination of zero and one at the same time, and measuring it "
    "collapses that combination into a definite value with real consequences for encryption. "
    "The rest of this essay is original analysis written for the smoke test to verify that "
    "partial copying produces a partial similarity score rather than a perfect match. "
    "Classical computers process information through transistors that switch between two "
    "states, and every program ever written reduces to enormous sequences of those switches. "
    "Quantum machines behave differently because their basic units follow the strange rules "
    "of subatomic particles, which means algorithms must be designed around probabilities "
    "rather than certainties. Engineers working on quantum hardware face a baffling array of "
    "competing technologies, from superconducting circuits chilled to near absolute zero to "
    "trapped ions suspended in electromagnetic fields, and nobody yet knows which approach "
    "will scale to the millions of qubits that useful computation may demand. Errors are the "
    "central problem: qubits are fragile, decohere within microseconds, and require elaborate "
    "correction schemes that consume most of the machine's capacity just keeping the remaining "
    "qubits honest. Skeptics note that similar skepticism greeted the first transistors, which "
    "were also unreliable laboratory curiosities once. Optimists answer that the physics has "
    "already been proven in demonstrations, and only engineering remains, though that phrase "
    "hides decades of work. For students entering computing today, the field offers a rare "
    "chance to watch a paradigm shift in real time, with all the false starts and dead ends "
    "that such transitions always involve. The history of technology suggests that the "
    "winners are rarely obvious in advance, and the safest prediction is simply that the "
    "future of computation will be stranger than any of us currently expects. Whatever "
    "emerges, the students who understand both the software abstractions and the quantum "
    "hardware underneath will be best prepared for whatever comes next in computing."
)


def main() -> None:
    from app.server import serve

    port = 8399
    base = f"http://127.0.0.1:{port}"
    httpd = serve(host="127.0.0.1", port=port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    # ---- static pages ----
    status, page = http("GET", base + "/")
    assert status == 200 and "TurnitOut" in page, "index should serve"

    # ---- anonymous scan (pasted text) ----
    status, data = http("POST", base + "/api/scan", {"text": ESSAY, "inCorpus": True})
    assert status == 200 and data.get("token"), data
    token = data["token"]
    assert "/" not in token and len(token) >= 20, "token should be URL-safe and unguessable"

    result = wait_done(base, token)
    assert result["status"] == "done", result
    assert result["similarity"] > 0.08, f"expected corpus overlap, got {result['similarity']}"
    assert result.get("aiScore") is not None, "AI analysis expected for 300+ prose words"

    _, data = http("GET", f"{base}/api/report/{token}")
    assert data["document"]["token"] == token
    assert data["report"]["sources"], "should match the sample corpus"
    assert data["aiReport"] and data["aiReport"]["aiScore"] is not None
    assert data["text"] == ESSAY

    # unknown token -> 404
    status, _ = http("GET", base + "/api/report/nosuchtoken123456")
    assert status == 404

    # ---- file upload scan (private: not in corpus) ----
    raw, ctype = multipart(
        {"file": ("uploaded_essay.txt", ESSAY.encode())},
        {"inCorpus": "0"})
    status, data = http("POST", base + "/api/scan", raw=raw, ctype=ctype)
    assert status == 200, data
    tok2 = data["token"]
    sub2 = wait_done(base, tok2)
    assert sub2["status"] == "done"
    _, data = http("GET", f"{base}/api/report/{tok2}")
    assert data["document"]["inCorpus"] is False, "private doc must not join corpus"
    assert data["document"]["filename"] == "uploaded_essay.txt"

    # ---- rescan ----
    status, data = http("POST", f"{base}/api/report/{tok2}/rescan", {"useWeb": False})
    assert status == 200 and data["ok"]

    # ---- validation ----
    status, data = http("POST", base + "/api/scan", {"text": "too short"})
    assert status == 400, data
    raw, ctype = multipart({"file": ("bad.exe", b"MZ binary")}, {})
    status, data = http("POST", base + "/api/scan", raw=raw, ctype=ctype)
    assert status == 400, data

    # ---- anonymized feed: no tokens, no text, no filenames ----
    status, data = http("GET", base + "/api/recent")
    assert status == 200 and data["totalScans"] >= 2
    for r in data["recent"]:
        blob = json.dumps(r)
        assert "token" not in blob and ESSAY[:20] not in blob and ".txt" not in blob

    # ---- health ----
    status, data = http("GET", base + "/api/health")
    assert status == 200 and data["ok"]

    httpd.shutdown()
    print("SMOKE OK — anonymous flow passed")


if __name__ == "__main__":
    main()
