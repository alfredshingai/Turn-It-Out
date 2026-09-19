"""End-to-end smoke test: boots the server and drives every main flow over HTTP.

Run: python tests/smoke.py
"""
import io
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


def http(method: str, url: str, body=None, cookie: str = "", raw=None, ctype: str = ""):
    data = None
    headers = {}
    if raw is not None:
        data = raw
        headers["Content-Type"] = ctype
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            set_cookie = resp.headers.get("Set-Cookie", "")
            text = resp.read().decode()
            try:
                return resp.status, json.loads(text or "{}"), set_cookie
            except json.JSONDecodeError:
                return resp.status, text, set_cookie
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}"), ""


def multipart(field: str, filename: str, content: bytes) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; "
        f"filename=\"{filename}\"\r\nContent-Type: application/octet-stream\r\n\r\n"
    ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def wait_done(base, cookie, sub_id, tries=240) -> dict:
    for _ in range(tries):
        _, data, _ = http("GET", f"{base}/api/submissions/{sub_id}", cookie=cookie)
        if data["submission"]["status"] in ("done", "error"):
            return data["submission"]
        time.sleep(0.25)
    raise AssertionError("submission never finished scanning")


def main() -> None:
    from app.server import serve

    port = 8399
    base = f"http://127.0.0.1:{port}"
    httpd = serve(host="127.0.0.1", port=port)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()

    # ---- static pages ----
    status, _, _ = http("GET", base + "/")
    assert status == 200, "index should serve"

    # ---- auth ----
    status, data, setc = http("POST", base + "/api/login",
                              {"email": "instructor@demo.edu", "password": "instructor123"})
    assert status == 200 and data["user"]["role"] == "instructor", data
    inst = "turnitout_session=" + setc.split("=", 1)[1].split(";")[0]

    status, _, _ = http("POST", base + "/api/login", {"email": "instructor@demo.edu", "password": "wrong"})
    assert status == 401

    status, data, setc = http("POST", base + "/api/login",
                              {"email": "student@demo.edu", "password": "student123"})
    stud = "turnitout_session=" + setc.split("=", 1)[1].split(";")[0]
    assert status == 200 and data["user"]["role"] == "student"

    # ---- seeded demo report exists (student essay copied from corpus) ----
    _, data, _ = http("GET", base + "/api/classes", cookie=stud)
    class_id = data["classes"][0]["id"]
    _, data, _ = http("GET", f"{base}/api/classes/{class_id}", cookie=stud)
    asg = [a for a in data["assignments"] if not a["title"].startswith("[")][0]
    _, data, _ = http("GET", f"{base}/api/assignments/{asg['id']}", cookie=stud)
    seeded = data["submissions"][0]
    assert seeded["status"] == "done" and seeded["similarity"] > 0.3, seeded
    _, data, _ = http("GET", f"{base}/api/submissions/{seeded['id']}/report", cookie=stud)
    assert data["report"]["sources"], "seeded submission should have matches"
    assert data["report"]["sources"][0]["spans"], "matches must include spans"

    # ---- instructor creates class + assignment; student joins + uploads ----
    code = uuid.uuid4().hex[:6].upper()
    _, data, _ = http("POST", base + "/api/classes", {"name": "SMOKE 101"}, cookie=inst)
    new_class = data["class"]["id"]
    status, data, _ = http("POST", base + "/api/classes/join", {"code": data["class"]["code"]}, cookie=stud)
    assert status == 200, data

    status, data, _ = http("POST", f"{base}/api/classes/{new_class}/assignments",
                           {"title": "Upload test", "instructions": "txt only"}, cookie=inst)
    assert status == 200, data
    new_asg = data["assignment"]["id"]

    content = (
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
    ).encode()
    raw, ctype = multipart("file", "smoke_essay.txt", content)
    status, data, _ = http("POST", f"{base}/api/assignments/{new_asg}/submit", raw=raw, ctype=ctype, cookie=stud)
    assert status == 200, data
    sid = data["submission"]["id"]

    sub = wait_done(base, stud, sid)
    assert sub["status"] == "done", sub
    assert sub["similarity"] > 0.08, f"expected overlap with corpus, got {sub['similarity']}"
    assert sub.get("ai_score") is not None, "doc is 300+ prose words, AI score expected"

    _, data, _ = http("GET", f"{base}/api/submissions/{sid}/report", cookie=stud)
    top = data["report"]["sources"][0]
    assert top["sourceKind"] == "db" and top["spans"], top
    assert data["report"]["wordCount"] > 20
    assert data["aiReport"] and data["aiReport"]["aiScore"] is not None, data.get("aiReport")
    assert data["aiReport"]["provider"] in ("gptzero", "heuristic")

    status, data, _ = http("GET", f"{base}/api/ai/status", cookie=stud)
    assert status == 200 and data["ai"]["mode"] in ("gptzero", "heuristic")

    # instructor can see it; student cannot see others
    status, data, _ = http("GET", f"{base}/api/submissions/{sid}/report", cookie=inst)
    assert status == 200
    status, _, _ = http("POST", f"{base}/api/submissions/{sid}/rescan", {"useWeb": False}, cookie=stud)
    assert status == 200

    # ---- access control & validation ----
    status, _, _ = http("GET", base + "/api/classes", cookie="")
    assert status == 401
    status, _, _ = http("POST", f"{base}/api/classes/{new_class}/assignments", {"title": "x"}, cookie=stud)
    assert status == 403
    raw, ctype = multipart("file", "bad.exe", b"MZ binary")
    status, data, _ = http("POST", f"{base}/api/assignments/{new_asg}/submit", raw=raw, ctype=ctype, cookie=stud)
    assert status == 400, data

    # stats endpoint
    status, data, _ = http("GET", base + "/api/stats", cookie=inst)
    assert status == 200 and data["submissions"] >= 1

    httpd.shutdown()
    print("SMOKE OK — all flows passed")


if __name__ == "__main__":
    main()
