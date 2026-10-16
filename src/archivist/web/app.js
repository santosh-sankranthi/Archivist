"use strict";

/* Archivist console — vanilla JS, no build step (M4a).
   Talks to the JSON API in archivist.webapp; the graph canvas is Cytoscape.
   Every view is a live read-only query; nothing is staged or cached. */

const $ = (id) => document.getElementById(id);
const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

async function api(url, opts) {
  const res = await fetch(url, opts);
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON error page */ }
  if (!res.ok) {
    const err = new Error((data && data.message) || `HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return data;
}

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
}

const collapse = (s) => String(s || "").replace(/\s+/g, " ").trim();
const trunc = (s, n) => (s && s.length > n ? s.slice(0, n - 1) + "…" : s || "");
const fmtTs = (t) => (t ? new Date(t * 1000).toISOString().slice(0, 16).replace("T", " ") : "");

const NODE_SIZE = { Repo: 30, Module: 22, File: 26, Symbol: 18, Commit: 15, Author: 15, Episode: 20, Fact: 20 };
const COLORS = {
  File: "#7d93a8", Symbol: "#c2bba9", Module: "#79a297",
  Repo: "#e0a458", Episode: "#9d7bc4", Fact: "#8fb0c9",
};

const state = { graph: "…", focus: null, weak: false, llmConfigured: false, lastSymbol: null };
let cy = null;
let fileMeta = null;

/* ---- graph rendering -------------------------------------------------- */

function initCy() {
  cy = cytoscape({
    container: $("canvas"),
    elements: [],
    // Deliberate: gentler zoom for demos. cytoscape prints a known one-line
    // startup warning for any non-default wheelSensitivity; we accept it.
    wheelSensitivity: 0.2,
    minZoom: 0.08,
    maxZoom: 3,
    style: [
      {
        selector: "node",
        style: {
          "background-color": (n) => COLORS[n.data("type")] || "#8a8f98",
          label: "data(label)",
          "font-family": "JetBrains Mono, ui-monospace, Menlo, monospace",
          "font-size": 9,
          color: "#cfcabb",
          "text-valign": "bottom",
          "text-halign": "center",
          "text-margin-y": 4,
          "text-wrap": "ellipsis",
          "text-max-width": "96px",
          "min-zoomed-font-size": 7,
          "text-background-color": "#0d0e10",
          "text-background-opacity": 0.72,
          "text-background-padding": "2px",
          width: "data(size)",
          height: "data(size)",
          "border-width": 1,
          "border-color": "#2a2e36",
        },
      },
      { selector: 'node[type="File"]', style: { shape: "round-rectangle" } },
      { selector: 'node[?focus]', style: { "border-width": 2, "border-color": "#e0a458", color: "#e0a458" } },
      {
        selector: "edge",
        style: {
          width: 1,
          "line-color": "#3a3f47",
          "curve-style": "bezier",
          "target-arrow-shape": "triangle",
          "target-arrow-color": "#3a3f47",
          "arrow-scale": 0.7,
          opacity: 0.85,
        },
      },
      {
        selector: "edge[?weak]",
        style: { "line-style": "dashed", "line-color": "#4a4436", "target-arrow-color": "#4a4436" },
      },
      { selector: "node:selected", style: { "overlay-opacity": 0.12, "overlay-color": "#e0a458" } },
    ],
    layout: { name: "preset" },
  });
  cy.on("tap", "node", (evt) => {
    const d = evt.target.data();
    if (d.nodeType === "Symbol") loadSymbol(d.file, d.qualname);
    else if (d.nodeType === "File") loadFile(d.path);
    else if (d.nodeType === "Module") loadModule(d.name);
  });
  cy.on("mouseover", "node", () => { document.body.style.cursor = "pointer"; });
  cy.on("mouseout", "node", () => { document.body.style.cursor = ""; });
}

function labelFor(node) {
  const p = node.props || {};
  switch (node.type) {
    case "File": return p.path;
    case "Symbol": return trunc(p.qualname, 30);
    case "Module": return p.name;
    case "Episode": return trunc(p.question || p.kind || p.id, 36);
    case "Fact": return trunc(p.text || p.id, 36);
    default: return p.sha || p.name || p.path || String(node.id);
  }
}

function elementsFrom(payload) {
  const nodes = payload.nodes.map((n) => {
    const data = {
      id: "n" + n.id,
      label: labelFor(n),
      type: n.type,
      nodeType: n.type,
      size: NODE_SIZE[n.type] || 18,
      focus: !!n.focus,
    };
    if (n.type === "Symbol") { data.file = n.props.file; data.qualname = n.props.qualname; }
    if (n.type === "File") { data.path = n.props.path; }
    if (n.type === "Module") { data.name = n.props.name; }
    return { data };
  });
  const edges = payload.edges.map((e) => ({
    data: {
      id: "e" + e.id,
      source: "n" + e.source,
      target: "n" + e.target,
      rel: e.rel,
      weak: e.props && e.props.weak === true,
    },
  }));
  return nodes.concat(edges);
}

function render(payload) {
  const els = elementsFrom(payload);
  cy.elements().remove();
  cy.add(els);
  $("canvas-hint").classList.toggle("hidden", els.length > 0);
  $("stage-stats").textContent = `${payload.nodes.length} nodes · ${payload.edges.length} edges`;
  const notes = payload.notes || [];
  const note = $("stage-note");
  if (notes.length) { note.textContent = notes.join("  ·  "); note.hidden = false; } else { note.hidden = true; }
  cy.resize();
  const layout = cy.layout({
    name: "cose",
    animate: !REDUCED,
    animationDuration: 350,
    nodeRepulsion: 16000,
    idealEdgeLength: 104,
    gravity: 0.55,
    componentSpacing: 72,
    padding: 30,
    numIter: 900,
    nodeOverlap: 14,
  });
  layout.one("layoutstop", () => cy.fit(undefined, 26));
  layout.run();
}

function crumbs(items) {
  const nav = $("crumbs");
  nav.textContent = "";
  items.forEach((item, i) => {
    if (i) nav.appendChild(el("span", "sep", "/"));
    const b = el("button", item.current ? "current" : "", item.label);
    if (!item.current && item.onClick) b.addEventListener("click", item.onClick);
    nav.appendChild(b);
  });
}

function showHint() {
  cy.elements().remove();
  $("canvas-hint").classList.remove("hidden");
  $("stage-stats").textContent = "";
  $("stage-note").hidden = true;
  hideFileMeta();
  state.focus = null;
  crumbs([{ label: state.graph, current: true }]);
}

/* ---- file meta overlay ------------------------------------------------ */

function fileMetaBox() {
  if (!fileMeta) {
    fileMeta = el("div", "file-meta");
    fileMeta.hidden = true;
    $("canvas").appendChild(fileMeta);
  }
  return fileMeta;
}

function hideFileMeta() { if (fileMeta) fileMeta.hidden = true; }

function showFileMeta(props, history, symbolCount) {
  const box = fileMetaBox();
  box.textContent = "";
  box.appendChild(el("div", "fm-title", props.path || ""));
  box.appendChild(el("div", "fm-sub", `${props.loc || "?"} loc · ${symbolCount} symbols`));
  if (history.length) {
    const list = el("ul", "fm-list");
    history.forEach((h) => {
      const li = el("li");
      li.appendChild(el("span", "fm-sha", h.sha));
      li.appendChild(el("span", "fm-ts", fmtTs(h.ts).slice(0, 10)));
      li.appendChild(el("span", "fm-diff", `+${h.added ?? "?"} −${h.removed ?? "?"}`));
      li.appendChild(el("span", "fm-sum", trunc(h.summary, 46)));
      list.appendChild(li);
    });
    box.appendChild(list);
  }
  box.hidden = false;
}

/* ---- view loaders ----------------------------------------------------- */

async function loadFile(path) {
  try {
    const payload = await api("/api/graph/file?path=" + encodeURIComponent(path));
    state.focus = { kind: "file", path };
    render(payload);
    crumbs([
      { label: state.graph, onClick: showHint },
      { label: path, current: true },
    ]);
    const symbolCount = payload.nodes.filter((n) => n.type === "Symbol").length;
    showFileMeta(payload.file || {}, payload.history || [], symbolCount);
  } catch (err) {
    toast("file view failed: " + err.message);
  }
}

async function loadSymbol(file, qualname) {
  try {
    state.lastSymbol = { file, qualname };
    const q = new URLSearchParams({ file, qualname, depth: "1", weak: state.weak ? "1" : "0" });
    const payload = await api("/api/graph/symbol?" + q.toString());
    state.focus = { kind: "symbol", file, qualname };
    hideFileMeta();
    render(payload);
    crumbs([
      { label: state.graph, onClick: showHint },
      { label: file, onClick: () => loadFile(file) },
      { label: qualname, current: true },
    ]);
    const s = payload.stats || {};
    $("stage-stats").textContent =
      `${payload.nodes.length} nodes · ${payload.edges.length} edges · ` +
      `${s.callers} callers / ${s.callees} callees (direct, strong)`;
  } catch (err) {
    toast("symbol view failed: " + err.message);
  }
}

async function loadModule(name) {
  try {
    const payload = await api("/api/graph/module?name=" + encodeURIComponent(name));
    state.focus = { kind: "module", name };
    hideFileMeta();
    render(payload);
    crumbs([
      { label: state.graph, onClick: showHint },
      { label: name, current: true },
    ]);
  } catch (err) {
    toast("module view failed: " + err.message);
  }
}

/* ---- sidebar ---------------------------------------------------------- */

function renderHubs(hubs) {
  const syms = $("hub-symbols");
  syms.textContent = "";
  (hubs.symbols || []).forEach((h) => {
    const b = el("button", "item");
    b.appendChild(el("span", "rank", `${h.callers}×`));
    b.appendChild(el("span", "name", h.qualname));
    b.appendChild(el("span", "sub", h.file));
    b.addEventListener("click", () => loadSymbol(h.file, h.qualname));
    const li = el("li");
    li.appendChild(b);
    syms.appendChild(li);
  });
  const mods = $("hub-modules");
  mods.textContent = "";
  (hubs.modules || []).forEach((m) => {
    const b = el("button", "item");
    b.appendChild(el("span", "rank", `${m.importers}×`));
    b.appendChild(el("span", "name", m.name));
    b.appendChild(el("span", "sub", `${m.importers} importers · ${m.sites} sites`));
    b.addEventListener("click", () => loadModule(m.name));
    const li = el("li");
    li.appendChild(b);
    mods.appendChild(li);
  });
}

function renderMemory(m) {
  const box = $("memory");
  box.textContent = "";
  const sessions = `${m.sessions} session${m.sessions === 1 ? "" : "s"}`;
  const facts = `${m.facts} fact${m.facts === 1 ? "" : "s"}`;
  box.appendChild(el("div", "big", `${sessions} · ${facts}`));
  if (m.latest) {
    const l = el("div", "latest");
    l.appendChild(el("div", "", `latest — ${m.latest.kind || "episode"} · ${fmtTs(m.latest.ts)}`));
    if (m.latest.question) l.appendChild(el("div", "", trunc(m.latest.question, 64)));
    box.appendChild(l);
  } else {
    box.appendChild(el("div", "latest", "nothing recorded yet"));
  }
}

async function refreshMemory() {
  try {
    const s = await api("/api/state");
    renderMemory(s.memory);
  } catch { /* non-fatal */ }
}

/* ---- ask + trace ------------------------------------------------------ */

function setAskStatus(text, cls) {
  const box = $("ask-status");
  box.textContent = text || "";
  box.className = "ask-status" + (cls ? " " + cls : "");
}

function renderTrace(answer) {
  const ol = $("trace");
  ol.textContent = "";
  const steps = answer.steps || [];
  if (!steps.length) {
    ol.appendChild(el("li", "empty", "no tool calls — direct answer"));
  }
  steps.forEach((step, i) => {
    const li = el("li", "step");
    const row = el("div", "row1");
    row.appendChild(el("span", "idx", String(i + 1)));
    row.appendChild(el("span", "tool", step.tool || "?"));
    row.appendChild(el("span", "params", trunc(collapse(JSON.stringify(step.params || {})), 64)));
    const ok = !step.error && step.status === "ok";
    row.appendChild(el("span", "status " + (step.error ? "bad" : ok ? "ok" : "warn"),
      step.error ? "error" : (step.status || "")));
    li.appendChild(row);
    const bits = [];
    if (step.count != null) bits.push(`${step.count} rows`);
    if (step.ms != null) bits.push(`${step.ms} ms`);
    if (bits.length) li.appendChild(el("div", "hint", bits.join(" · ")));
    if (step.notes && step.notes.length) li.appendChild(el("div", "note", step.notes.join(" · ")));
    if (step.error) li.appendChild(el("div", "note", trunc(step.error, 160)));
    const queries = (step.result && step.result.queries) || [];
    if (queries.length) {
      const det = el("details");
      det.appendChild(el("summary", "", `cypher · ${queries.length}`));
      queries.forEach((q) => {
        const pre = el("pre");
        pre.textContent =
          `-- ${q.tool || step.tool} · ${q.ms} ms · ${q.rows} rows\n` +
          collapse(q.cypher) + "\n" +
          `params ${JSON.stringify(q.params || {})}`;
        det.appendChild(pre);
      });
      li.appendChild(det);
    }
    ol.appendChild(li);
  });
  const queries = answer.queries || [];
  const totalMs = queries.reduce((n, q) => n + (q.ms || 0), 0);
  $("trace-totals").textContent = queries.length
    ? `${queries.length} queries · ${totalMs} ms`
    : "";
}

function renderAnswer(data) {
  const a = data.answer;
  const box = $("answer");
  box.textContent = "";
  box.classList.add("shown");
  box.appendChild(el("div", "atext", a.text || "(no answer produced)"));
  const queries = a.queries || [];
  const totalMs = queries.reduce((n, q) => n + (q.ms || 0), 0);
  const meta = el("div", "meta");
  meta.textContent =
    `${queries.length} queries · ${totalMs} ms · ${a.llm_calls} LLM calls` +
    (a.usage && a.usage.total_tokens ? ` · ${a.usage.total_tokens} tokens` : "");
  box.appendChild(meta);
  if (a.partial) box.appendChild(el("div", "meta", "note: step budget or LLM failure cut this run short"));
  if (data.episode_id) box.appendChild(el("div", "meta", `memory: session ${data.episode_id} recorded`));
  if (data.warnings && data.warnings.length) {
    box.appendChild(el("div", "meta", data.warnings.join("; ")));
  }
  renderTrace(a);
}

async function ask(question) {
  const btn = $("ask-submit");
  btn.disabled = true;
  setAskStatus("running agent — tools → graph → answer…");
  $("trace").textContent = "";
  $("trace-totals").textContent = "";
  $("answer").classList.remove("shown");
  try {
    const data = await api("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, remember: $("ask-remember").checked }),
    });
    setAskStatus("");
    renderAnswer(data);
    refreshMemory();
  } catch (err) {
    if (err.status === 503) {
      setAskStatus("no LLM key configured — the explorer works without one; add ARCHIVIST_LLM_* to enable chat.", "warn");
    } else {
      setAskStatus("ask failed: " + err.message, "bad");
    }
  } finally {
    btn.disabled = false;
  }
}

/* ---- toast ------------------------------------------------------------ */

let toastTimer = null;
function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 5000);
}

/* ---- wiring + boot ---------------------------------------------------- */

function wire() {
  $("search-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const q = $("search-input").value.trim();
    if (!q) return;
    const results = $("search-results");
    results.textContent = "";
    try {
      const data = await api("/api/graph/find?q=" + encodeURIComponent(q));
      const items = [];
      (data.symbols || []).forEach((s) => items.push({
        name: s.qualname,
        sub: `${s.file}:${s.lineno} · ${s.kind}`,
        go: () => loadSymbol(s.file, s.qualname),
      }));
      (data.files || []).forEach((f) => items.push({
        name: f.path,
        sub: `${f.loc} loc`,
        go: () => loadFile(f.path),
      }));
      if (!items.length) {
        const li = el("li");
        li.appendChild(el("div", "empty", "no matches"));
        results.appendChild(li);
      }
      items.slice(0, 14).forEach((it) => {
        const b = el("button", "item");
        b.appendChild(el("span", "name", it.name));
        b.appendChild(el("span", "sub", it.sub));
        b.addEventListener("click", it.go);
        const li = el("li");
        li.appendChild(b);
        results.appendChild(li);
      });
      if (items.length > 14) {
        const li = el("li");
        li.appendChild(
          el("div", "empty", `showing first 14 of ${items.length} — refine to narrow`)
        );
        results.appendChild(li);
      }
    } catch (err) {
      toast("search failed: " + err.message);
    }
  });

  $("ask-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const question = $("ask-input").value.trim();
    if (!question) return;
    if (!state.llmConfigured) {
      setAskStatus("no LLM key configured — set ARCHIVIST_LLM_* in the environment or repo .env, then restart.", "warn");
      return;
    }
    ask(question);
  });

  $("btn-fit").addEventListener("click", () => {
    if (cy && cy.elements().length) cy.fit(undefined, 30);
  });

  $("btn-weak").addEventListener("click", () => {
    state.weak = !state.weak;
    $("btn-weak").setAttribute("aria-pressed", String(state.weak));
    if (state.lastSymbol && state.focus && state.focus.kind === "symbol") {
      loadSymbol(state.lastSymbol.file, state.lastSymbol.qualname);
    }
  });

  window.addEventListener("resize", () => { if (cy) cy.resize(); });
}

async function boot() {
  initCy();
  wire();
  crumbs([{ label: "…", current: true }]);
  try {
    const s = await api("/api/state");
    state.graph = s.graph;
    state.llmConfigured = s.llm.configured;
    $("chip-graph").textContent = s.graph;
    const totalNodes = s.nodes.reduce((n, r) => n + r.count, 0);
    const totalEdges = s.edges.reduce((n, r) => n + r.count, 0);
    $("chip-counts").textContent = `${totalNodes.toLocaleString()} nodes · ${totalEdges.toLocaleString()} edges`;
    const chip = $("chip-llm");
    chip.textContent = s.llm.configured ? `llm: ${s.llm.model}` : "llm: not configured";
    chip.classList.add(s.llm.configured ? "on" : "off");
    renderMemory(s.memory);
  } catch (err) {
    toast("state failed: " + err.message);
    state.graph = "graph";
  }
  try {
    const o = await api("/api/graph/overview");
    renderHubs(o.hubs || {});
  } catch (err) {
    toast("overview failed: " + err.message);
  }
  showHint();
}

boot();
