/* TurnitOut SPA — vanilla JS, no dependencies. */
const app = document.getElementById("app");
const state = { user: null, route: null, flash: "", reportTab: "similarity", aiStatus: null };

const API = {
  async req(method, url, body, isForm) {
    const opts = { method, headers: {} };
    if (body && isForm) opts.body = body;
    else if (body) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
    const res = await fetch(url, opts);
    let data = {};
    try { data = await res.json(); } catch (e) {}
    if (!res.ok) { const err = new Error(data.error || res.statusText); err.status = res.status; throw err; }
    return data;
  },
  get: (u) => API.req("GET", u),
  post: (u, b) => API.req("POST", u, b),
  postForm: (u, fd) => API.req("POST", u, fd, true),
};

function h(html) { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content; }
function esc(s) { return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function pct(x) { return Math.round((x || 0) * 100); }
function simClass(v) { return v >= 0.5 ? "bad" : v >= 0.25 ? "warn" : "good"; }
function simColor(v) { return v >= 0.5 ? "#b91c1c" : v >= 0.25 ? "#b45309" : "#15803d"; }
const PALETTE = ["#1d4ed8", "#b91c1c", "#047857", "#7c3aed", "#b45309", "#0e7490", "#be185d", "#4d7c0f"];

function navigate(route) { location.hash = route; }
window.addEventListener("hashchange", render);

function topbar(active) {
  const u = state.user;
  const initials = u.name.split(/\s+/).map(w => w[0]).slice(0, 2).join("").toUpperCase();
  return `
  <div class="topbar">
    <div class="logo" style="cursor:pointer" onclick="navigate('/classes')">🔍 Turnit<span>Out</span></div>
    <nav>
      <button class="${active === "classes" ? "active" : ""}" onclick="navigate('/classes')">Classes</button>
      <button class="${active === "stats" ? "active" : ""}" onclick="navigate('/stats')">Dashboard</button>
    </nav>
    <div class="userchip"><b>${esc(u.name)}</b> · ${u.role}
      <div class="avatar">${esc(initials)}</div>
      <button class="btn secondary" onclick="logout()">Sign out</button>
    </div>
  </div>`;
}

function layout(active, inner) {
  return topbar(active) + `<main>${state.flash ? `<div class="flash show">${esc(state.flash)}</div>` : ""}${inner}</main>`;
}

async function logout() {
  await API.post("/api/logout", {});
  state.user = null;
  navigate("/login"); render();
}

// ---------------------------------------------------------------- views

function loginView(mode) {
  const isReg = mode === "register";
  return `    <div class="loginwrap"><div class="loginbox card">
    <div class="brand">🔍 Turnit<span>Out</span></div>
    <div class="tag">Similarity + AI-writing checks for classes</div>
    <div class="tabs" style="justify-content:center">
      <button class="${!isReg ? "active" : ""}" onclick="navigate('/login');render()">Sign in</button>
      <button class="${isReg ? "active" : ""}" onclick="navigate('/register');render()">Create account</button>
    </div>
    <form onsubmit="return doAuth(event, '${isReg ? "register" : "login"}')">
      ${isReg ? `<label>Your name</label><input id="f-name" required placeholder="Jane Doe">` : ""}
      <label>Email</label><input id="f-email" type="email" required placeholder="you@school.edu">
      <label>Password</label><input id="f-password" type="password" required minlength="6" placeholder="••••••••">
      ${isReg ? `
      <label>I am a</label>
      <select id="f-role"><option value="student">Student</option><option value="instructor">Instructor</option></select>
      <div id="invite-row" style="display:none">
        <label>Invite code</label>
        <input id="f-invite" placeholder="Required for instructor accounts">
      </div>` : ""}
      <div class="error-box" id="auth-error"></div>
      <div class="mt2"><button class="btn" style="width:100%">${isReg ? "Create account" : "Sign in"}</button></div>
    </form>
    <div class="demo-hint">
      <b>Demo accounts</b><br>
      Instructor — <code>instructor@demo.edu</code> / <code>instructor123</code><br>
      Student — <code>student@demo.edu</code> / <code>student123</code>
    </div>
  </div></div>`;
}

async function doAuth(ev, kind) {
  ev.preventDefault();
  const err = document.getElementById("auth-error");
  err.classList.remove("show");
  const body = {
    email: document.getElementById("f-email").value,
    password: document.getElementById("f-password").value,
  };
  if (kind === "register") {
    body.name = document.getElementById("f-name").value;
    body.role = document.getElementById("f-role").value;
    const inv = document.getElementById("f-invite");
    if (inv) body.inviteCode = inv.value;
  }
  try {
    const data = await API.post(`/api/${kind}`, body);
    state.user = data.user;
    navigate("/classes"); render();
  } catch (e) { err.textContent = e.message; err.classList.add("show"); }
  return false;
}

async function statsView() {
  const s = (await API.get("/api/stats"));
  const stat = (label, val, cls = "", extra = "") => `
    <div class="card"><h3>${label}</h3><div class="statnum ${cls}">${val}</div>${extra}</div>`;
  const avg = pct(s.avgSimilarity);
  const roleRows = state.user.role === "instructor"
    ? `<div class="grid cols4">
        ${stat("Classes", s.classes)}
        ${stat("Students", s.students)}
        ${stat("Submissions", s.submissions)}
        ${stat("Flagged ≥25%", s.flagged, s.flagged ? "warn" : "")}
        ${stat("AI-flagged ≥50%", s.aiFlagged ?? 0, s.aiFlagged ? "warn" : "")}
      </div>`
    : `<div class="grid cols4">
        ${stat("My classes", s.classes)}
        ${stat("My submissions", s.submissions)}
        ${stat("Flagged ≥25%", s.flagged, s.flagged ? "warn" : "")}
        ${stat("AI-flagged ≥50%", s.aiFlagged ?? 0, s.aiFlagged ? "warn" : "")}
      </div>`;
  return layout("stats", `
    <h1>Dashboard</h1>
    <p class="sub">${state.user.role === "instructor" ? "Overview of your classes and similarity results." : "Your submissions at a glance."}</p>
    <div class="grid cols4">
      ${stat("Average similarity", avg + "%", simClass(s.avgSimilarity))}
    </div>
    <div class="mt1"></div>
    ${roleRows}
    <div class="card mt2"><h3>How scoring works</h3>
      <p class="meta" style="margin:6px 0 0;line-height:1.6">
        Similarity = share of your document's words found in matching passages (6-word fingerprints,
        merged into highlighted spans) across other submissions and — when online — web pages found by phrase search.
        Under 15% is usually incidental overlap; 25%+ deserves a closer look; 50%+ suggests substantial copying.
      </p>
    </div>`);
}

async function classesView() {
  const data = await API.get("/api/classes");
  const isInstructor = state.user.role === "instructor";
  const cards = data.classes.map(c => `
    <div class="card clickable" style="cursor:pointer" onclick="navigate('/classes/${c.id}')">
      <h3>${esc(c.name)}</h3>
      ${isInstructor ? `<div class="meta">Join code: <b>${esc(c.code)}</b></div>` : `<div class="meta">Code ${esc(c.code)}</div>`}
      <div class="meta mt1">${c.students ?? 0} students · ${c.assignments ?? 0} assignments</div>
    </div>`).join("");
  return layout("classes", `
    <div class="toolbar">
      <div><h1 class="mt0">Classes</h1>
      <p class="sub" style="margin:0">${isInstructor ? "Create a class, share the join code, and review similarity reports." : "Join a class with its code, then submit to assignments."}</p></div>    ${isInstructor ? `<button class="btn" onclick="showCreateClass()">+ New class</button>
        <button class="btn secondary" onclick="showInvites()">Invites</button>`
        : `<button class="btn" onclick="showJoinClass()">+ Join class</button>`}
    </div>
    <div id="class-form-slot"></div>
    ${data.classes.length ? `<div class="grid cols2 mt1">${cards}</div>`
      : `<div class="card empty">No classes yet. ${isInstructor ? "Create one to get started." : "Ask your instructor for a join code."}</div>`}`);
}

function showCreateClass() {
  document.getElementById("class-form-slot").innerHTML = `
    <div class="card mt1"><h3>Create a class</h3>
      <form onsubmit="return createClass(event)">
        <label>Class name</label><input id="cn" required placeholder="e.g. HIST 202: Modern Europe">
        <div class="mt2"><button class="btn">Create</button> <button type="button" class="btn secondary" onclick="this.closest('.card').remove()">Cancel</button></div>
      </form></div>`;
  document.getElementById("cn").focus();
}
async function createClass(ev) {
  ev.preventDefault();
  const name = document.getElementById("cn").value;
  const data = await API.post("/api/classes", { name });
  state.flash = `Class created. Share join code: ${data.class.code}`;
  render();
}

function showJoinClass() {
  document.getElementById("class-form-slot").innerHTML = `
    <div class="card mt1"><h3>Join a class</h3>
      <form onsubmit="return joinClass(event)">
        <label>Join code</label><input id="jc" required placeholder="e.g. ENG101" style="text-transform:uppercase">
        <div class="mt2"><button class="btn">Join</button> <button type="button" class="btn secondary" onclick="this.closest('.card').remove()">Cancel</button></div>
      </form></div>`;
  document.getElementById("jc").focus();
}
async function joinClass(ev) {
  ev.preventDefault();
  const code = document.getElementById("jc").value;
  await API.post("/api/classes/join", { code });
  state.flash = "Joined class.";
  render();
}

async function showInvites() {
  const data = await API.get("/api/invites");
  document.getElementById("class-form-slot").innerHTML = `
    <div class="card mt1"><h3>Instructor invite codes</h3>
      <p class="meta">Share a code with a colleague so they can register as an instructor.
      Students never need one.</p>
      <button class="btn" onclick="createInvite()">+ Generate code</button>
      <div class="mt1">
      ${(data.invites || []).map(i => `
        <div class="metric-chip" style="display:block;border-radius:8px;margin-bottom:6px">
          <b>${esc(i.code)}</b>
          ${i.used_at ? `· used by ${esc(i.used_by_name || "?")}` : "· unused"}
        </div>`).join("") || '<span class="muted">No codes generated yet.</span>'}
      </div>
      <div class="mt2"><button class="btn secondary" onclick="this.closest('.card').remove()">Close</button></div>
    </div>`;
}

async function createInvite() {
  const data = await API.post("/api/invites", {});
  state.flash = `Invite code created: ${data.invite.code}`;
  showInvites();
  const flash = document.querySelector(".flash");
  if (flash) flash.textContent = state.flash;
}

async function classDetailView(id) {
  const data = await API.get(`/api/classes/${id}`);
  const isInstructor = state.user.role === "instructor";
  const asg = data.assignments.map(a => `
    <tr class="clickable" onclick="navigate('/assignments/${a.id}')">
      <td><b>${esc(a.title)}</b><div class="meta">${esc(a.instructions || "")}</div></td>
      <td class="muted">${esc(a.due_at || "—")}</td>
      <td>${a.submission_count}</td>
      <td>›</td>
    </tr>`).join("");
  const roster = (data.roster || []).map(r => `
    <tr><td><b>${esc(r.name)}</b><div class="meta">${esc(r.email)}</div></td>
        <td>${r.submissions}</td></tr>`).join("");
  return layout("classes", `
    <p class="sub" style="margin:0"><a href="#/classes">← All classes</a></p>
    <div class="toolbar">
      <div><h1 class="mt0">${esc(data.class.name)}</h1>
      <p class="sub" style="margin:0">Join code: <b>${esc(data.class.code)}</b></p></div>
      ${isInstructor ? `<button class="btn" onclick="showNewAssignment(${id})">+ New assignment</button>` : ""}
    </div>
    <div id="asg-form-slot"></div>
    <h2>Assignments</h2>
    <div class="card" style="padding:4px 10px">
      ${asg ? `<table><thead><tr><th>Assignment</th><th>Due</th><th>Submissions</th><th></th></tr></thead><tbody>${asg}</tbody></table>`
            : `<div class="empty">${isInstructor ? "No assignments yet — create the first one." : "No assignments yet."}</div>`}
    </div>
    ${isInstructor ? `<h2>Roster</h2><div class="card" style="padding:4px 10px">
      ${roster ? `<table><thead><tr><th>Student</th><th>Submissions</th></tr></thead><tbody>${roster}</tbody></table>`
               : `<div class="empty">No students have joined yet. Share the join code: <b>${esc(data.class.code)}</b></div>`}
    </div>` : ""}`);
}

function showNewAssignment(classId) {
  document.getElementById("asg-form-slot").innerHTML = `
    <div class="card mt1"><h3>New assignment</h3>
      <form onsubmit="return createAssignment(event, ${classId})">
        <label>Title</label><input id="at" required placeholder="Essay 2: …">
        <label>Instructions</label><textarea id="ai" rows="3" placeholder="What should students write?"></textarea>
        <label>Due date</label><input id="ad" type="date">
        <div class="mt2"><button class="btn">Create</button>
        <button type="button" class="btn secondary" onclick="this.closest('.card').remove()">Cancel</button></div>
      </form></div>`;
  document.getElementById("at").focus();
}
async function createAssignment(ev, classId) {
  ev.preventDefault();
  await API.post(`/api/classes/${classId}/assignments`, {
    title: document.getElementById("at").value,
    instructions: document.getElementById("ai").value,
    dueAt: document.getElementById("ad").value,
  });
  state.flash = "Assignment created.";
  render();
}

async function assignmentDetailView(id) {
  const data = await API.get(`/api/assignments/${id}`);
  const isInstructor = state.user.role === "instructor";
  const rows = data.submissions.map(s => `
    <tr class="clickable" onclick="navigate('/submissions/${s.id}')">
      <td><b>${esc(s.filename)}</b><div class="meta">${esc(s.student_name || "")}</div></td>
      <td><span class="badge ${esc(s.status)}">${esc(s.status)}</span>${s.error ? `<div class="meta" style="color:var(--bad)">${esc(s.error)}</div>` : ""}</td>
      <td>${new Date(s.created_at * 1000).toLocaleString()}</td>
      <td>${simCell(s)}</td>
      <td>${aiCell(s)}</td>
      <td>›</td>
    </tr>`).join("");
  return layout("classes", `
    <p class="sub" style="margin:0"><a href="#/classes/${data.assignment.class_id}">← ${esc("Back to class")}</a></p>
    <div class="toolbar">
      <div><h1 class="mt0">${esc(data.assignment.title)}</h1>
      <p class="sub" style="margin:0">${esc(data.assignment.instructions || "")}</p></div>
      ${isInstructor ? "" : `<button class="btn" onclick="showUpload(${id})">⬆ Submit document</button>`}
    </div>
    <div id="upload-slot"></div>
    <h2>${isInstructor ? "All submissions" : "My submissions"}</h2>
    <div class="card" style="padding:4px 10px">
      ${rows ? `<table>
        <thead><tr><th>Document</th><th>Status</th><th>Submitted</th><th>Similarity</th><th>AI</th><th></th></tr></thead>
        <tbody>${rows}</tbody></table>`
      : `<div class="empty">${isInstructor ? "No submissions yet." : "Nothing submitted yet — upload a .txt, .md, .docx or .pdf."}</div>`}
    </div>`);
}

function simCell(s) {
  if (s.status !== "done") return `<span class="muted">—</span>`;
  const v = s.similarity;
  return `<div class="simcell"><div class="simbar"><i style="width:${pct(v)}%;background:${simColor(v)}"></i></div>
          <span class="simval" style="color:${simColor(v)}">${pct(v)}%</span></div>`;
}

function aiColor(v) { return v >= 0.8 ? "#b91c1c" : v >= 0.5 ? "#ea580c" : v >= 0.2 ? "#b45309" : "#15803d"; }

function aiCell(s) {
  if (s.status !== "done" || s.ai_score === null || s.ai_score === undefined)
    return `<span class="muted" title="No AI analysis">—</span>`;
  const v = s.ai_score;
  // Turnitin's rule: 1–19% is too false-positive-prone to display as a number.
  if (v > 0.005 && v <= 0.19)
    return `<span class="simval" style="color:var(--muted)" title="Low-reliability range (1–19%) — score suppressed, shown as *%">*%</span>`;
  return `<div class="simcell"><div class="simbar"><i style="width:${pct(v)}%;background:${aiColor(v)}"></i></div>
          <span class="simval" style="color:${aiColor(v)}">${pct(v)}%</span></div>`;
}

function showUpload(assignmentId) {
  document.getElementById("upload-slot").innerHTML = `
    <div class="card mt1"><h3>Submit a document</h3>
      <p class="meta">Accepted: .txt, .md, .docx, .pdf (text-based, max 10 MB). Scanning starts automatically.</p>
      <form onsubmit="return submitDoc(event, ${assignmentId})">
        <input type="file" id="uf" accept=".txt,.md,.docx,.pdf" required
               style="border:1px dashed var(--line);padding:14px;border-radius:8px;background:#fafbfe">
        <div class="mt2"><button class="btn">Upload &amp; scan</button>
        <button type="button" class="btn secondary" onclick="this.closest('.card').remove()">Cancel</button></div>
      </form></div>`;
}
async function submitDoc(ev, assignmentId) {
  ev.preventDefault();
  const fileInput = document.getElementById("uf");
  const fd = new FormData();
  fd.append("file", fileInput.files[0]);
  const btn = ev.target.querySelector("button.btn");
  btn.disabled = true; btn.textContent = "Uploading…";
  try {
    await API.postForm(`/api/assignments/${assignmentId}/submit`, fd);
    state.flash = "Uploaded — scanning now. The report appears when the scan finishes.";
  } catch (e) { state.flash = ""; alert(e.message); }
  render();
}

async function submissionView(id) {
  const [data, aiSt] = await Promise.all([
    API.get(`/api/submissions/${id}/report`),
    state.aiStatus ? Promise.resolve({ ai: state.aiStatus }) : API.get("/api/ai/status"),
  ]);
  state.aiStatus = aiSt.ai;
  const sub = data.submission;
  let body = "";
  if (sub.status === "queued" || sub.status === "processing") {
    body = `<div class="card empty">Scanning… <button class="btn secondary" style="margin-left:10px" onclick="render()">Refresh</button></div>`;
  } else if (sub.status === "error") {
    body = `<div class="card" style="border-color:#fecaca"><h3 style="color:var(--bad)">Scan failed</h3>
            <p class="meta">${esc(sub.error || "Unknown error")}</p>
            <button class="btn" onclick="rescan(${id})">Retry scan</button></div>`;
  } else if (!data.report) {
    body = `<div class="card empty">No report yet.</div>`;
  } else {
    const tab = state.reportTab === "ai" ? "ai" : "similarity";
    body = `
      <div class="tabs">
        <button class="${tab === "similarity" ? "active" : ""}" onclick="state.reportTab='similarity';render()">Similarity</button>
        <button class="${tab === "ai" ? "active" : ""}" onclick="state.reportTab='ai';render()">AI writing</button>
      </div>
      ${tab === "ai" ? aiReportBody(data, aiSt.ai) : reportBody(id, data)}`;
  }
  return layout("classes", `
    <p class="sub" style="margin:0"><a href="#" onclick="history.back();return false">← Back</a></p>
    <div class="toolbar">
      <div><h1 class="mt0" style="margin-bottom:2px">Similarity Report</h1>
      <p class="sub" style="margin:0">${esc(sub.filename)} · ${sub.wordCount} words</p></div>
      <button class="btn secondary" onclick="rescan(${id})">↻ Rescan</button>
    </div>
    ${body}`);
}

function aiLabel(cls) {
  return {
    likely_ai: "Likely AI-generated",
    ai_paraphrased: "Likely AI-generated + AI-paraphrased",
    mixed: "Mixed AI & human",
    probably_human: "Probably human",
    human: "Human",
    insufficient_text: "Not enough qualifying prose",
  }[cls] || cls;
}

function aiReportBody(data, aiStatus) {
  const ai = data.aiReport;
  if (!ai) return `<div class="card empty">No AI analysis stored for this submission. Use “Rescan” to run it.</div>`;
  const v = ai.aiScore;
  if (v === null || v === undefined) {
    return `<div class="card empty">${esc(ai.note || "Document too short for AI analysis.")}</div>`;
  }
  const providerTag = ai.provider === "gptzero" ? `GPTZero API`
    : ai.provider === "lmstudio" ? `Local model scoring (LM Studio)`
    : `Local heuristics ${aiStatus && aiStatus.mode === "gptzero" ? "(API unreachable — fell back)" : ""}`;

  // Turnitin-style asterisk rule: suppress 1–19% scores (high false-positive range).
  const suppressed = v > 0.005 && v <= 0.19;
  const displayScore = suppressed ? "*%" : pct(v) + "%";
  const scoreColor = suppressed ? "var(--muted)" : aiColor(v);

  // Document with AI sentence highlighting.
  const text = data.text || "";
  const scored = (ai.sentenceScores || []).filter(s => s.aiScore !== null && s.aiScore >= 0.5);
  let html = esc(text);
  if (scored.length) {
    scored.sort((a, b) => a.start - b.start);
    let out = "", pos = 0;
    for (const sp of scored) {
      if (sp.start < pos) continue;
      out += esc(text.slice(pos, sp.start));
      out += `<mark class="hl" style="background:${aiColor(sp.aiScore)}22;border-bottom-color:${aiColor(sp.aiScore)}">` +
             esc(text.slice(sp.start, sp.end)) + `</mark>`;
      pos = sp.end;
    }
    out += esc(text.slice(pos));
    html = out;
  }

  const metrics = ai.metrics ? Object.entries(ai.metrics).map(([k, val]) =>
    `<span class="metric-chip"><b>${esc(k)}</b> ${esc(String(val))}</span>`).join("") : "";
  const qual = ai.qualifyingSentences !== undefined && ai.totalSentences !== undefined
    ? `<div class="srcmeta mt1">Qualifying prose: ${ai.qualifyingSentences} of ${ai.totalSentences}
       sentences (${ai.qualifyingWords} words). Lists, tables, code, citations and fragments are
       excluded from analysis — same rule Turnitin applies.</div>` : "";

  return `
  <div class="report-layout">
    <div class="srclist">
      <div class="card">
        <div class="gauge">
          <div class="big" style="color:${scoreColor}">${displayScore}</div>
          <div class="lbl"><b>${esc(aiLabel(ai.classification))}</b><br>
            ${esc(providerTag)} · probabilistic — <b>not proof of misconduct</b></div>
        </div>
        ${suppressed ? `<p class="srcmeta mt1" style="color:var(--warn)">
          Scores between 1–19% are suppressed to *% (Turnitin's rule) because this range
          has the highest false-positive risk. Treat as “no reliable AI signal”.</p>` : ""}
        ${qual}
      </div>
      <div class="card"><h3>Signals</h3><div class="metric-row">${metrics || '<span class="muted">—</span>'}</div></div>
      <div class="card"><p class="meta" style="margin:0;line-height:1.55">
        Highlighted sentences scored ≥50% likely AI-generated. Detectors — ours, GPTZero's,
        Turnitin's — misfire on non-native writing, formal/repetitive prose, and short texts.
        A high score is a reason to <b>talk with the student about their writing process</b>
        (drafts, version history, ability to explain the work), never a verdict on its own.
        ${ai.bypasserScore >= 0.55 ? "<br><br><b>Bypasser signals detected:</b> style markers suggest possible AI-paraphrasing-tool use. Verify with the student before drawing conclusions." : ""}</p></div>
    </div>
    <div>
      <div class="legend"><span><span class="chip" style="background:#ea580c22;border-bottom:2px solid #ea580c"></span>sentence likely AI-generated</span></div>
      <div class="docview">${html}</div>
    </div>
  </div>`;
}

function reportBody(id, data) {
  const rep = data.report;
  const v = rep.overall;
  const sorted = [...rep.sources].sort((a, b) => b.matchedWords - a.matchedWords);
  const totalWords = rep.wordCount || 1;
  const colorOf = (i) => PALETTE[i % PALETTE.length];

  const srcCards = sorted.map((s, i) => {
    const share = pct(s.matchedWords / totalWords);
    return `<div class="card src-card" data-idx="${i}" onclick="selectSource(${i})">
      <div><span class="srcnum" style="background:${colorOf(i)}">${i + 1}</span>
      <span class="srctitle">${esc(s.sourceName)}</span></div>
      <div class="srcmeta">${s.sourceKind === "web" ? "🌐 Web" : "🗄 Database"} · ${share}% of document
        ${s.sourceUrl ? `· <a href="${esc(s.sourceUrl)}" target="_blank" rel="noopener noreferrer" onclick="event.stopPropagation()">link</a>` : ""}</div>
    </div>`;
  }).join("");

  const legend = sorted.map((s, i) =>
    `<span><span class="chip" style="background:${colorOf(i)}"></span>Source ${i + 1}</span>`).join("");

  // Build highlighted document text.
  let text = data.text || "";
  let html = esc(text);
  if (sorted.length) {
    // Re-highlight using char spans: build overlay per span with source color.
    const spans = [];
    sorted.forEach((s, i) => s.spans.forEach(sp => spans.push({ start: sp.start, end: sp.end, color: colorOf(i), idx: i })));
    spans.sort((a, b) => a.start - b.start || b.end - a.end);
    // Rebuild with highlighting.
    let out = "", pos = 0;
    const escSlice = (a, b) => esc(text.slice(a, b));
    for (const sp of spans) {
      if (sp.start < pos) continue; // overlapping earlier span; skip to keep HTML valid
      out += escSlice(pos, sp.start);
      out += `<mark class="hl" style="background:${sp.color}22;border-bottom-color:${sp.color}" data-src="${sp.idx}">` +
             escSlice(sp.start, sp.end) + `</mark>`;
      pos = sp.end;
    }
    out += escSlice(pos, text.length);
    html = out;
  }

  return `
  <div class="report-layout">
    <div class="srclist">
      <div class="card">
        <div class="gauge">
          <div class="big" style="color:${simColor(v)}">${pct(v)}%</div>
          <div class="lbl">Similarity — share of this document found in matching passages</div>
        </div>
      </div>
      <div class="legend">${legend || '<span class="muted">No matching sources</span>'}</div>
      ${srcCards || `<div class="card empty">No matches found. 🎉</div>`}
    </div>
    <div>
      <div class="legend"><span><span class="chip" style="background:#b91c1c22;border-bottom:2px solid #b91c1c"></span>highlight = matching passage</span>
        <span class="muted">Click a source to jump to its matches.</span></div>
      <div class="docview" id="docview">${html}</div>
    </div>
  </div>`;
}

function selectSource(idx) {
  document.querySelectorAll(".src-card").forEach(c => c.classList.toggle("selected", c.dataset.idx === String(idx)));
  const firstMark = document.querySelector(`#docview mark[data-src="${idx}"]`);
  if (firstMark) firstMark.scrollIntoView({ behavior: "smooth", block: "center" });
}

async function rescan(id) {
  await API.post(`/api/submissions/${id}/rescan`, { useWeb: true });
  state.flash = "Rescan queued.";
  render();
}

// ---------------------------------------------------------------- router

async function render() {
  const route = location.hash.replace(/^#/, "") || (state.user ? "/classes" : "/login");
  try {
    if (!state.user) {
      const me = await API.get("/api/me").catch(() => null);
      state.user = me && me.user;
    }
    if (!state.user) {
      if (route === "/register") app.innerHTML = loginView("register");
      else app.innerHTML = loginView("login");
      return;
    }
    let m;
    if (route === "/stats") return void (app.innerHTML = await statsView());
    if (route === "/login" || route === "/register" || route === "/") return void (navigate("/classes"), render());
    if ((m = route.match(/^\/classes\/(\d+)$/))) return void (app.innerHTML = await classDetailView(m[1]));
    if ((m = route.match(/^\/assignments\/(\d+)$/))) return void (app.innerHTML = await assignmentDetailView(m[1]));
    if ((m = route.match(/^\/submissions\/(\d+)$/))) return void (app.innerHTML = await submissionView(m[1]));
    app.innerHTML = await classesView();
  } catch (e) {
    if (e.status === 401) { state.user = null; navigate("/login"); render(); return; }
    app.innerHTML = `<div class="loginwrap"><div class="card" style="max-width:480px">
      <h3>Something went wrong</h3><p class="meta">${esc(e.message)}</p>
      <button class="btn" onclick="navigate('/classes');render()">Go home</button></div></div>`;
  }
}

render();
