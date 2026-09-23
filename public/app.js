/* TurnitOut SPA — anonymous text checking. No accounts, no tracking. */
const app = document.getElementById("app");
const state = { route: null, reportTab: "similarity", aiStatus: null, polling: null };

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

function esc(s) { return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function pct(x) { return Math.round((x || 0) * 100); }
function simClass(v) { return v >= 0.5 ? "bad" : v >= 0.25 ? "warn" : "good"; }
function simColor(v) { return v >= 0.5 ? "#b91c1c" : v >= 0.25 ? "#b45309" : "#15803d"; }
function aiColor(v) { return v >= 0.8 ? "#b91c1c" : v >= 0.5 ? "#ea580c" : v >= 0.2 ? "#b45309" : "#15803d"; }
const PALETTE = ["#1d4ed8", "#b91c1c", "#047857", "#7c3aed", "#b45309", "#0e7490", "#be185d", "#4d7c0f"];

function navigate(route) { location.hash = route; }
window.addEventListener("hashchange", render);

function topbar() {
  return `
  <div class="topbar">
    <div class="logo" style="cursor:pointer" onclick="navigate('/')">🔍 Turnit<span>Out</span></div>
    <nav></nav>
    <div class="userchip"><a href="https://github.com/alfredshingai/Turn-It-Out" target="_blank" rel="noopener noreferrer" class="muted">GitHub ↗</a></div>
  </div>`;
}

function layout(inner) {
  return topbar() + `<main>${inner}
  <p class="footer-note">TurnitOut is free and open source · scans are anonymous · your report link is the only key to it
  · <a href="https://github.com/alfredshingai/Turn-It-Out" target="_blank" rel="noopener noreferrer">contribute ↗</a></p></main>`;
}

// ---------------------------------------------------------------- home

function homeView() {
  return layout(`
    <div class="hero">
      <h1>Paste text. Get the report.</h1>
      <p class="sub" style="margin:0 auto 18px;max-width:640px">Free, anonymous similarity + AI-writing checks for <b>any</b> text —
      essays, articles, reports, letters. No account, no signup, no email. Your report lives at a private link only you have.</p>
      <div class="card scanbox">
        <textarea id="scan-text" rows="9" placeholder="Paste any text here (20–30,000 words)…"></textarea>
        <div class="scanrow">
          <input type="file" id="scan-file" accept=".txt,.md,.docx,.pdf"
                 style="border:1px dashed var(--line);padding:10px;border-radius:8px;background:#fafbfe;flex:1">
          <button class="btn" onclick="doScan()">Check text →</button>
        </div>
        <label class="corpus-opt"><input type="checkbox" id="in-corpus" checked>
          Include this text in the shared anonymized corpus — every scan makes the tool smarter for everyone
          (uncheck to keep it fully private; it will still be checked <i>against</i> the corpus either way).</label>
        <div class="error-box" id="scan-error"></div>
      </div>
      <div class="card scanbox" id="enhancer-box" style="margin-top:16px">
        <h3 style="margin:0 0 4px">Writing enhancer — clarify your own draft</h3>
        <p class="meta" style="margin:0 0 10px">For text you wrote or have the right to edit.
        Fixes stiff, repetitive phrasing and explains each change. <b>Not a bypass tool</b> —
        AI-use disclosure and your institution's policies still apply.</p>
        <textarea id="enh-text" rows="6" placeholder="Paste your draft here (5–30,000 words)…"></textarea>
        <div class="scanrow">
          <select id="enh-mode" style="border:1px solid var(--line);padding:10px;border-radius:8px;background:#fafbfe">
            <option value="clarity">Clarity (default)</option>
            <option value="concise">Concise</option>
            <option value="natural">Natural voice</option>
            <option value="formal">Formal tone</option>
          </select>
          <button class="btn secondary" onclick="doHumanize()">Enhance writing →</button>
          <button class="btn secondary" onclick="copyEnhanced()" id="enh-copy" style="display:none">Copy result</button>
        </div>
        <div class="error-box" id="enh-error"></div>
        <div id="enh-output" style="display:none;margin-top:10px">
          <textarea id="enh-result" rows="6" readonly style="background:#f8fafc"></textarea>
          <div class="srcmeta mt1" id="enh-meta"></div>
          <div id="enh-edits" class="metric-row" style="margin-top:8px"></div>
          <p class="srcmeta mt1" id="enh-note" style="color:var(--muted)"></p>
        </div>
      </div>
      <div class="how grid cols3">
        <div class="card"><h3>1 · Paste or upload</h3><p class="meta">Text or files (.txt, .md, .docx, .pdf). Nothing is required but the words themselves.</p></div>
        <div class="card"><h3>2 · We check it</h3><p class="meta">Against the shared corpus of past scans, the live web, and AI-writing patterns (perplexity, burstiness, style).</p></div>
        <div class="card"><h3>3 · Read the report</h3><p class="meta">Similarity % with highlighted passages, AI-writing likelihood with flagged sentences — at a private link you can share.</p></div>
      </div>
      <div id="recent-slot"></div>
    </div>`);
}

async function loadRecent() {
  try {
    const data = await API.get("/api/recent");
    const el = document.getElementById("recent-slot");
    if (!el) return;
    const chips = (data.recent || []).map(r => {
      const ai = (r.aiScore === null || r.aiScore === undefined) ? "·" :
        (r.aiScore > 0.005 && r.aiScore <= 0.19 ? "*%" : pct(r.aiScore) + "% AI");
      return `<span class="recent-chip">${r.words}w · ${pct(r.similarity)}% sim · ${ai}</span>`;
    }).join("");
    el.innerHTML = `
      <div class="recent">
        <h2>Live — ${data.totalScans.toLocaleString()} scans run so far</h2>
        <p class="meta">Anonymized feed of recent checks. No text, no filenames, no links — just proof the engine is alive.</p>
        <div class="chips">${chips || '<span class="muted">No scans yet.</span>'}</div>
      </div>`;
  } catch (e) { /* non-critical */ }
}

async function doScan() {
  const err = document.getElementById("scan-error");
  err.classList.remove("show");
  const text = document.getElementById("scan-text").value.trim();
  const fileInput = document.getElementById("scan-file");
  const inCorpus = document.getElementById("in-corpus").checked;
  try {
    let data;
    if (fileInput.files[0]) {
      const fd = new FormData();
      fd.append("file", fileInput.files[0]);
      fd.append("inCorpus", inCorpus ? "1" : "0");
      data = await API.postForm("/api/scan", fd);
    } else if (text) {
      data = await API.post("/api/scan", { text, inCorpus });
    } else {
      throw new Error("Paste text or choose a file first");
    }
    navigate("/" + data.token);
  } catch (e) {
    err.textContent = e.message;
    err.classList.add("show");
  }
}

async function doHumanize() {
  const err = document.getElementById("enh-error");
  const out = document.getElementById("enh-output");
  err.classList.remove("show");
  out.style.display = "none";
  const text = document.getElementById("enh-text").value.trim();
  const mode = document.getElementById("enh-mode").value;
  if (!text) { err.textContent = "Paste your draft first"; err.classList.add("show"); return; }
  err.textContent = "Enhancing…";
  err.classList.add("show");
  try {
    const data = await API.post("/api/humanize", { text, mode });
    document.getElementById("enh-result").value = data.enhanced;
    document.getElementById("enh-meta").textContent =
      `${data.editCount} suggestion${data.editCount === 1 ? "" : "s"} · ` +
      `via ${data.provider === "lmstudio" ? "local LM Studio" : "offline rules"} · mode: ${data.mode}`;
    document.getElementById("enh-edits").innerHTML =
      (data.edits || []).slice(0, 12).map(e =>
        `<span class="metric-chip"><b>${esc(e.type)}</b> ${esc(e.original)} → ${esc(e.suggestion)}</span>`
      ).join("") || '<span class="muted">No changes needed — already reads naturally.</span>';
    document.getElementById("enh-note").textContent = data.note || "";
    out.style.display = "block";
    document.getElementById("enh-copy").style.display = "";
    err.classList.remove("show");
  } catch (e) {
    err.textContent = e.message;
    err.classList.add("show");
  }
}

function copyEnhanced() {
  const v = document.getElementById("enh-result").value;
  if (v) navigator.clipboard.writeText(v);
}

// ---------------------------------------------------------------- report

async function reportView(token) {
  const [data, aiSt] = await Promise.all([
    API.get(`/api/report/${token}`),
    state.aiStatus ? Promise.resolve({ ai: state.aiStatus }) : API.get("/api/health"),
  ]);
  const doc = data.document;
  let body = "";
  if (doc.status === "queued" || doc.status === "processing") {
    if (state.polling) clearTimeout(state.polling);
    state.polling = setTimeout(() => render(), 2500);
    body = `<div class="card empty">Scanning… the report appears automatically.</div>`;
  } else if (doc.status === "error") {
    body = `<div class="card" style="border-color:#fecaca"><h3 style="color:var(--bad)">Scan failed</h3>
            <p class="meta">${esc(doc.error || "Unknown error")}</p></div>`;
  } else {
    const tab = state.reportTab === "ai" ? "ai" : "similarity";
    body = `
      <div class="tabs">
        <button class="${tab === "similarity" ? "active" : ""}" onclick="state.reportTab='similarity';render()">Similarity</button>
        <button class="${tab === "ai" ? "active" : ""}" onclick="state.reportTab='ai';render()">AI writing</button>
      </div>
      ${tab === "ai" ? aiReportBody(data) : reportBody(data)}`;
  }
  return layout(`
    <div class="toolbar">
      <div><h1 class="mt0" style="margin-bottom:2px">Report</h1>
      <p class="sub" style="margin:0">${esc(doc.filename)} · ${doc.wordCount} words
        ${doc.inCorpus ? "" : "· <b>private</b> (not in shared corpus)"}</p></div>
      <div class="row-actions">
        <button class="btn secondary" onclick="copyLink()">Copy link</button>
        <button class="btn secondary" onclick="rescan('${token}')">↻ Rescan</button>
      </div>
    </div>
    ${body}`);
}

function copyLink() {
  navigator.clipboard.writeText(location.href).then(() => {
    state.flashText = "Link copied — anyone with it can view this report.";
    const el = document.querySelector(".flash");
    if (el) { el.textContent = state.flashText; el.classList.add("show"); }
  });
}

async function rescan(token) {
  await API.post(`/api/report/${token}/rescan`, { useWeb: true });
  render();
}

function reportBody(data) {
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
      <div class="srcmeta">${s.sourceKind === "web" ? "🌐 Web" : "🗄 Shared corpus"} · ${share}% of document
        ${s.sourceUrl ? `· <a href="${esc(s.sourceUrl)}" target="_blank" rel="noopener noreferrer" onclick="event.stopPropagation()">link</a>` : ""}</div>
    </div>`;
  }).join("");

  const legend = sorted.map((s, i) =>
    `<span><span class="chip" style="background:${colorOf(i)}"></span>Source ${i + 1}</span>`).join("");

  const text = data.text || "";
  let html = esc(text);
  if (sorted.length) {
    const spans = [];
    sorted.forEach((s, i) => s.spans.forEach(sp => spans.push({ start: sp.start, end: sp.end, color: colorOf(i), idx: i })));
    spans.sort((a, b) => a.start - b.start || b.end - a.end);
    let out = "", pos = 0;
    for (const sp of spans) {
      if (sp.start < pos) continue;
      out += esc(text.slice(pos, sp.start));
      out += `<mark class="hl" style="background:${sp.color}22;border-bottom-color:${sp.color}" data-src="${sp.idx}">` +
             esc(text.slice(sp.start, sp.end)) + `</mark>`;
      pos = sp.end;
    }
    out += esc(text.slice(pos));
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

function aiReportBody(data) {
  const ai = data.aiReport;
  if (!ai) return `<div class="card empty">No AI analysis stored for this document. Use “Rescan” to run it.</div>`;
  const v = ai.aiScore;
  if (v === null || v === undefined) {
    return `<div class="card empty">${esc(ai.note || "Document too short for AI analysis.")}</div>`;
  }
  const providerTag = ai.provider === "gptzero" ? `GPTZero API`
    : ai.provider === "lmstudio" ? `Local model scoring (LM Studio)`
    : `Local heuristics`;

  const suppressed = v > 0.005 && v <= 0.19;
  const displayScore = suppressed ? "*%" : pct(v) + "%";
  const scoreColor = suppressed ? "var(--muted)" : aiColor(v);

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
       sentences (${ai.qualifyingWords} words). Lists, tables, code, citations and fragments are excluded.</div>` : "";

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
          Scores between 1–19% are suppressed to *% (the same rule Turnitin uses) because this
          range has the highest false-positive risk. Treat as “no reliable AI signal”.</p>` : ""}
        ${qual}
      </div>
      <div class="card"><h3>Signals</h3><div class="metric-row">${metrics || '<span class="muted">—</span>'}</div></div>
      <div class="card"><p class="meta" style="margin:0;line-height:1.55">
        Highlighted sentences scored ≥50% likely AI-generated. All AI detectors — including
        Turnitin's — misfire on non-native writing, formal/repetitive prose, and short texts.
        A high score is a reason to ask <b>how the text was written</b>, never a verdict on its own.
        ${ai.bypasserScore >= 0.55 ? "<br><br><b>Bypasser signals detected:</b> style markers suggest possible AI-paraphrasing-tool use." : ""}</p></div>
    </div>
    <div>
      <div class="legend"><span><span class="chip" style="background:#ea580c22;border-bottom:2px solid #ea580c"></span>sentence likely AI-generated</span></div>
      <div class="docview">${html}</div>
    </div>
  </div>`;
}

// ---------------------------------------------------------------- router

async function render() {
  if (state.polling) { clearTimeout(state.polling); state.polling = null; }
  const route = decodeURIComponent(location.hash.replace(/^#\/?/, ""));
  try {
    if (route) return void (app.innerHTML = await reportView(route));
    app.innerHTML = homeView();
    loadRecent();
  } catch (e) {
    app.innerHTML = layout(`<div class="card empty" style="margin-top:30px">
      <h3>Report not found</h3>
      <p class="meta">${esc(e.message)}</p>
      <button class="btn" onclick="navigate('/')">Start a new check</button></div>`);
  }
}

render();
