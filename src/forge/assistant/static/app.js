"use strict";
/* Monday Studio: a desktop workspace for Monday's coding and computer agent.
   Talks to the local server over one WebSocket (/ws) and a small REST API; see docs/studio-protocol.md. */

/* the app window (window_native.py) shows macOS's frosted sidebar behind the page */
if (new URLSearchParams(location.search).has("native")) document.documentElement.classList.add("native");

/* ================= helpers ================= */
const $ = (id) => document.getElementById(id);
const esc = (s) => MD.esc(String(s == null ? "" : s));
const icon = (name, cls = "") => `<svg class="${cls}"><use href="#i-${name}"/></svg>`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const gb = (n) => (n ? `${(n / 1e9).toFixed(1)} GB` : "");
const kfmt = (n) => (n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : String(n || 0));
const base = (p) => String(p || "").replace(/\/+$/, "").split("/").pop() || p;
const tilde = (p) => String(p || "").replace(/^(\/private)?\/Users\/[^/]+/, "~");
function ago(t) {
  const s = Math.max(0, Date.now() / 1000 - (t || 0));
  if (s < 60) return "now";
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  if (s < 604800) return `${Math.floor(s / 86400)}d`;
  return `${Math.floor(s / 604800)}w`;
}
const shortPath = (p) => { const t = tilde(p).split("/"); return t.length > 4 ? `${t[0]}/…/${t.slice(-2).join("/")}` : t.join("/"); };

async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: {} };
  if (opts.body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(opts.body); }
  if (opts.form) { init.body = opts.form; delete init.headers["Content-Type"]; }
  const r = await fetch(path, init);
  if (!r.ok) {
    let detail = r.statusText;
    try { const j = await r.json(); detail = j.detail || detail; } catch (e) { /* not JSON */ }
    const err = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    err.status = r.status;
    throw err;
  }
  return r.status === 204 ? null : r.json();
}

function toast(text, level = "info", ms = 3800) {
  const t = document.createElement("div");
  t.className = `toast ${level}`;
  t.textContent = text;
  $("toasts").appendChild(t);
  setTimeout(() => t.remove(), level === "error" ? ms + 2500 : ms);
}

async function copy(text) {
  try { await navigator.clipboard.writeText(text); toast("Copied"); } catch (e) { toast("Couldn’t copy", "error"); }
}

function prettyModel(name) {
  if (!name) return "Model";
  if (name.startsWith("openai:")) return name.slice(7).split("/").pop().replace(/\.gguf$/i, "");
  if (name.startsWith("aistudio:")) return name.slice(9).replace(/^gemma-/, "Gemma ").replace(/-it$/, "").replace(/-/g, " ").replace(/\b(\d+b)\b/gi, (m) => m.toUpperCase()).replace(/\ba(\d+)b\b/i, "A$1B");
  const [b, tag] = name.split(":");
  if (b === "gemma4") return `Gemma 4 ${(tag || "").split("-")[0].toUpperCase()}`;
  return name;
}

/* ================= state ================= */
const S = {
  name: "Monday", model: {}, prefs: { approve: "ask", search: true, think: false }, voice: false, state: "idle",
  chats: [], chatId: null, chat: null, view: "session",
  live: {},               // chat id -> Turn while a run streams
  running: new Set(),     // chat ids with a run in progress
  approvals: {},          // id -> approval message, pending
  mode: "code", workspace: null, recentWs: [], branches: {},
  attachments: [], ctxUsed: 0, ws: null, pendingNew: false, demo: null,
};
const input = $("input");

/* ================= theme ================= */
function theme() { try { return localStorage.getItem("forge-theme") || "system"; } catch (e) { return "system"; } }
function setTheme(t) {
  try { localStorage.setItem("forge-theme", t); } catch (e) { /* private mode */ }
  if (t === "light" || t === "dark") document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme;
}

/* ================= WebSocket ================= */
let wsQueue = [];
function connect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  S.ws = ws;
  ws.onopen = () => { wsQueue.forEach((m) => ws.send(JSON.stringify(m))); wsQueue = []; };
  ws.onmessage = (e) => { try { onMessage(JSON.parse(e.data)); } catch (err) { console.error(err); } };
  ws.onclose = () => { setStatus("bad", "Reconnecting…"); setTimeout(connect, 1200); };
}
function send(msg) {
  if (S.demo && Demo.intercept(msg)) return;
  if (S.ws && S.ws.readyState === 1) S.ws.send(JSON.stringify(msg)); else wsQueue.push(msg);
}

function onMessage(m) {
  switch (m.type) {
    case "hello":
      S.name = m.name || "Monday"; S.voice = !!m.voice; applyModel(m.model || {}); applyPrefs(m.prefs || {});
      if (m.running_chat) S.running.add(m.running_chat);
      setState(m.state); renderSessions();
      break;
    case "state": setState(m.state); break;
    case "model": applyModel(m); break;
    case "prefs": applyPrefs(m.prefs); break;
    case "chat_created": onChatCreated(m); break;
    case "user_message": onUserMessage(m); break;
    case "run": onRun(m); break;
    case "approval": onApproval(m); break;
    case "approval_done": onApprovalDone(m); break;
    case "toast": toast(m.text, m.level === "error" ? "error" : "info"); break;
    case "notice": toast(m.text); break;
    case "transcript": if (m.text) { input.value = (input.value ? input.value.trimEnd() + " " : "") + m.text; autosize(); input.focus(); } break;
    case "pull": if (window.onPull) window.onPull(m); break;
    case "eval": if (window.onEval) window.onEval(m); break;
    default: break; // voice-agent events (level, caption, wake, cursor...) are for the HUD
  }
}

/* ================= model, prefs, status ================= */
function applyModel(m) {
  S.model = m || {};
  $("model-name").textContent = prettyModel(S.model.model);
  $("model-dot").className = "dot " + (S.model.offline ? "bad" : "ok");
  $("status-model").textContent = prettyModel(S.model.model);
  updateStatusSub();
  updateCtx();
}
function applyPrefs(p) {
  S.prefs = { ...S.prefs, ...p };
  const auto = S.prefs.approve === "auto";
  $("perm-text").textContent = auto ? "Auto-approve" : "Ask for approval";
  $("perm-btn").classList.toggle("auto", auto);
  document.querySelectorAll("#perm-menu [data-approve]").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.approve === S.prefs.approve)));
  $("voice-pill").hidden = !(S.voice && S.prefs.voice_mode);
  if ($("voice-btn")) syncTray();
  if (S.view === "settings" && window.syncSettings) window.syncSettings();
}
async function setPref(k, v) {
  S.prefs[k] = v; applyPrefs({});
  try { applyPrefs(await api("/api/prefs", { method: "POST", body: { [k]: v } })); } catch (e) { toast(e.message, "error"); }
}
function setState(st) { S.state = st || "idle"; updateStatusSub(); }
function setStatus(cls, text) { $("status-dot").className = "dot " + cls; $("status-sub").textContent = text; }
function updateStatusSub() {
  const route = { local: "Ollama on this Mac", openai: "OpenAI-compatible server", aistudio: "Google AI Studio" }[S.model.route] || "";
  if (S.model.offline) return setStatus("bad", "Ollama isn’t running");
  const busy = { thinking: "Thinking…", acting: "Working…", waiting: "Waiting for you", speaking: "Speaking", listening: "Listening" }[S.state];
  setStatus(busy ? "wait" : "ok", busy || route || "Ready");
}
function updateCtx() {
  const ctx = S.model.context || 0, el = $("ctx");
  el.hidden = !ctx || !S.chatId;
  if (el.hidden) return;
  const used = Math.min(S.ctxUsed, ctx), f = used / ctx;
  $("ctx-ring").style.strokeDashoffset = String(50.3 * (1 - f));
  $("ctx-text").textContent = `${kfmt(used)} / ${kfmt(ctx)}`;
  el.classList.toggle("warn", f > .8);
  el.dataset.tip = `Context used by this session: ${used.toLocaleString()} of ${ctx.toLocaleString()} tokens`;
}

/* ================= sidebar ================= */
async function loadChats() {
  try { S.chats = await api("/api/chats"); } catch (e) { S.chats = []; }
  renderSessions();
}
const SHOW = 5;  // sessions shown per project before "Show more"
const opened = new Set(), closed = new Set();
function renderSessions() {
  const q = $("side-search").value.trim().toLowerCase();
  const list = S.chats.filter((c) => !q || c.title.toLowerCase().includes(q) || (c.workspace || "").toLowerCase().includes(q));
  const projects = new Map(), computer = [];
  for (const c of list) {
    if (c.mode === "code" && c.workspace) {
      if (!projects.has(c.workspace)) projects.set(c.workspace, []);
      projects.get(c.workspace).push(c);
    } else computer.push(c);
  }
  for (const w of S.recentWs || []) if (!q && !projects.has(w.path)) projects.set(w.path, []);
  const box = $("sessions");
  const row = (c) => {
    const run = S.running.has(c.id);
    return `<a class="sess ${c.id === S.chatId ? "on" : ""} ${run ? "running" : ""}" href="#/s/${c.id}" data-id="${c.id}" ${c.id === S.chatId ? 'aria-current="page"' : ""}>
      ${run ? '<span class="run-dot" aria-hidden="true"></span>' : ""}<span class="t">${esc(c.title)}</span>
      <span class="when">${run ? "In progress" : ago(c.updated)}</span>
      <button class="more" data-more="${c.id}" aria-label="Session actions" aria-haspopup="menu">${icon("dots")}</button></a>`;
  };
  const group = (key, label, ic, items) => {
    const isClosed = closed.has(key), all = opened.has(key);
    const shown = all ? items : items.slice(0, SHOW);
    return `<div class="proj ${isClosed ? "closed" : ""}"><button class="proj-h" data-proj="${esc(key)}" aria-expanded="${!isClosed}" title="${esc(key.startsWith("~") ? label : key)}">${icon(isClosed ? "folder" : ic)}<span>${esc(label)}</span></button>
      ${shown.map(row).join("")}${items.length > shown.length ? `<button class="show-more" data-all="${esc(key)}">Show more</button>` : ""}
      ${items.length ? "" : '<span class="proj-empty">No sessions</span>'}</div>`;
  };
  let html = `<div class="sec-h">Projects<button class="icon-btn" id="add-proj" aria-label="Open a project folder" data-tip="Open a folder">${icon("plus")}</button></div>`;
  html += projects.size ? [...projects.entries()].map(([p, items]) => group(p, base(p), "folder-open", items)).join("")
    : `<div class="side-empty">Open a project folder to work on code.</div>`;
  if (computer.length) html += `<div class="sec-h">Computer</div>` + group("~computer", "On this Mac", "monitor", computer);
  if (q && !list.length) html = `<div class="side-empty">No sessions match “${esc(q)}”.</div>`;
  box.innerHTML = html;
}
$("side-search").addEventListener("input", renderSessions);
$("side-search-btn").onclick = () => {
  const box = $("side-search-box"), open = box.hidden;
  box.hidden = !open; $("side-search-btn").hidden = open; $("side-search-btn").setAttribute("aria-expanded", String(open));
  if (open) $("side-search").focus();
};
$("side-search").addEventListener("blur", () => { if (!$("side-search").value) { $("side-search-box").hidden = true; $("side-search-btn").hidden = false; } });
$("sessions").addEventListener("click", (e) => {
  const ph = e.target.closest("[data-proj]");
  if (ph) { const k = ph.dataset.proj; closed.has(k) ? closed.delete(k) : closed.add(k); renderSessions(); return; }
  const sm = e.target.closest("[data-all]");
  if (sm) { opened.add(sm.dataset.all); renderSessions(); return; }
  if (e.target.closest("#add-proj")) { pickFolder(); return; }
  const more = e.target.closest("[data-more]");
  if (more) { e.preventDefault(); e.stopPropagation(); sessionMenu(more); return; }
  if (window.innerWidth <= 900) closeDrawer();
});

let floating = null;
function closeFloating() { if (floating) { floating.el.remove(); floating.btn?.setAttribute("aria-expanded", "false"); floating = null; } }
function sessionMenu(btn) {
  const id = btn.dataset.more;
  if (floating && floating.btn === btn) return closeFloating();
  closeFloating();
  const el = document.createElement("div");
  el.className = "menu"; el.setAttribute("role", "menu");
  el.innerHTML = `<button class="row-btn" data-act="rename" role="menuitem">${icon("edit")}Rename</button>
    <button class="row-btn danger" data-act="delete" role="menuitem">${icon("trash")}<span class="lbl">Delete</span></button>`;
  document.body.appendChild(el);
  const r = btn.getBoundingClientRect();
  Object.assign(el.style, { position: "fixed", top: `${Math.min(r.bottom + 4, innerHeight - 100)}px`, left: `${Math.min(r.left, innerWidth - 250)}px` });
  floating = { el, btn };
  btn.setAttribute("aria-expanded", "true");
  el.querySelector("button").focus();
  el.onclick = async (e) => {
    const b = e.target.closest("[data-act]");
    if (!b) return;
    if (b.dataset.act === "rename") { closeFloating(); renameSession(id); }
    if (b.dataset.act === "delete") {
      if (!b.dataset.sure) { b.dataset.sure = "1"; b.querySelector(".lbl").textContent = "Click again to delete"; return; }
      closeFloating();
      await api(`/api/chats/${id}`, { method: "DELETE" }).catch((er) => toast(er.message, "error"));
      S.chats = S.chats.filter((c) => c.id !== id);
      if (S.chatId === id) location.hash = "#/";
      renderSessions();
    }
  };
}
function renameSession(id) {
  const a = document.querySelector(`.sess[data-id="${id}"]`);
  const c = S.chats.find((x) => x.id === id);
  if (!a || !c) return;
  const t = a.querySelector(".t");
  const inp = document.createElement("input");
  inp.value = c.title; inp.setAttribute("aria-label", "Session name");
  t.replaceWith(inp); inp.focus(); inp.select();
  let done = false;
  const finish = async (save) => {
    if (done) return; done = true;
    const title = inp.value.trim();
    if (save && title && title !== c.title) {
      c.title = title;
      await api(`/api/chats/${id}`, { method: "PATCH", body: { title } }).catch((er) => toast(er.message, "error"));
      if (S.chatId === id) $("top-title").textContent = title;
    }
    renderSessions();
  };
  inp.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); });
  inp.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); finish(true); } if (e.key === "Escape") { e.stopPropagation(); finish(false); } });
  inp.addEventListener("blur", () => finish(true));
}

function openDrawer() { $("app").classList.add("drawer"); $("scrim").hidden = false; }
function closeDrawer() { $("app").classList.remove("drawer"); $("scrim").hidden = true; }
$("side-open").onclick = () => (window.innerWidth <= 900 ? openDrawer() : $("app").classList.remove("collapsed"));
$("side-close").onclick = () => (window.innerWidth <= 900 ? closeDrawer() : $("app").classList.add("collapsed"));
$("scrim").onclick = closeDrawer;

/* ================= routing ================= */
const PAGE_VIEWS = ["models", "skills", "evals", "train", "settings"];
function route() {
  closeFloating(); closeMenus();
  const h = location.hash.replace(/^#\/?/, "");
  const [a, b] = h.split("/");
  document.querySelectorAll("[data-nav]").forEach((x) => x.classList.toggle("on", x.dataset.nav === a));
  if (PAGE_VIEWS.includes(a)) {
    S.view = a; S.chatId = null; S.chat = null;
    showView(a);
    $("top-title").textContent = a[0].toUpperCase() + a.slice(1); $("top-ws").hidden = true; $("mode-seg").hidden = true; updateCtx();
    window.PAGES[a]();
    renderSessions();
    return;
  }
  S.view = "session"; showView("session"); $("mode-seg").hidden = false;
  if (a === "demo") return Demo.start(b === "computer" ? "computer" : "code");
  if (S.demo) { Demo.stop(); S.running.delete("demo-" + S.demo); delete S.live["demo-" + S.demo]; }
  S.demo = null;
  if (a === "s" && b) openChat(b); else startScreen();
}
function showView(v) {
  document.querySelectorAll("main > .view").forEach((x) => (x.hidden = x.id !== `view-${v}`));
  if (window.innerWidth <= 900) closeDrawer();
}

/* ================= top bar ================= */
function setTop(chat) {
  $("top-title").textContent = chat ? chat.title : "";
  const mode = chat ? chat.mode || "computer" : null;
  const w = $("top-ws");
  w.hidden = !(chat && mode === "code" && chat.workspace);
  if (!w.hidden) {
    const br = S.branches[chat.workspace];
    w.innerHTML = `${icon("folder")}<span class="p" title="${esc(chat.workspace)}">${esc(base(chat.workspace))}</span>${br ? `${icon("branch")}<span>${esc(br)}</span>` : ""}`;
    if (br === undefined) loadBranch(chat.workspace);
  }
  syncTray();
}
async function loadBranch(path) {
  S.branches[path] = "";
  try { const r = await api("/api/workspaces/check", { method: "POST", body: { path } }); S.branches[path] = r.branch || ""; } catch (e) { /* endpoint optional */ }
  if (S.chat && S.chat.workspace === path) setTop(S.chat); else syncTray();
}

/* ================= start screen ================= */
const SUGGEST = {
  code: [["bug", "Find and fix the failing tests in this repo."], ["doc", "Explain how this project is structured, file by file."], ["bolt", "Add input validation to the signup form, with tests."]],
  computer: [["sun", "What’s my battery and the weather in Karachi?"], ["doc", "Find my project report PDF and summarize it."], ["music", "Play lofi on YouTube and set the volume to 30."]],
};
async function startScreen() {
  S.chatId = null; S.chat = null; S.ctxUsed = 0;
  $("thread").hidden = true; $("thread").innerHTML = "";
  $("start").hidden = false;
  $("view-session").classList.add("is-start");
  setTop(null); updateCtx(); renderSessions(); syncMode();
  renderStart(); setSending();
  loadRecentWs();
  setTimeout(() => input.focus(), 30);
}
function renderStart() {
  const code = S.mode === "code", ws = S.workspace;
  $("start").innerHTML = `<div class="mark" aria-hidden="true">${icon("mark")}</div>
    ${code ? `<h1>Let’s build</h1><button class="sub" id="start-proj" aria-haspopup="menu">${esc(ws ? base(ws.path) : "Choose a project")}${icon("chev")}</button>`
           : `<h1>What should we work on?</h1>`}`;
  $("suggest").innerHTML = SUGGEST[S.mode].map(([ic, t]) => `<button type="button" class="sugg" data-suggest="${esc(t)}">${icon(ic)}<span>${esc(t)}</span></button>`).join("");
  syncTray();
}
/* the tray under the composer: project and branch (Code), and the voice agent */
function syncTray() {
  const mode = S.chatId ? (S.chat && S.chat.mode) || S.mode : S.mode;
  const path = S.chatId ? S.chat && S.chat.workspace : S.workspace && S.workspace.path;
  const code = mode === "code";
  $("proj-pop").hidden = !code;
  $("proj-text").textContent = path ? base(path) : "Choose project";
  const br = path ? S.branches[path] : "";
  if (code && path && br === undefined) loadBranch(path);
  $("branch-chip").hidden = !(code && br);
  $("branch-text").textContent = br || "";
  const on = !!(S.voice && S.prefs.voice_mode);
  $("voice-btn").classList.toggle("on", on);
  $("voice-text").textContent = on ? "Voice agent on · Stop" : "Voice agent";
  $("voice-btn").disabled = !S.voice;
  $("voice-btn").title = S.voice ? (on ? "Stop the hands-free voice agent" : "Start hands-free Monday: tap your talk key and just talk, with the on-screen orb and spoken replies") : "Monday was started without voice";
}
async function loadRecentWs() {
  let recent = [];
  try { recent = (await api("/api/workspaces")).recent || []; } catch (e) {
    const seen = new Set();
    recent = S.chats.filter((c) => c.mode === "code" && c.workspace && !seen.has(c.workspace) && seen.add(c.workspace)).map((c) => ({ path: c.workspace, name: base(c.workspace) }));
  }
  S.recentWs = recent;
  recent.forEach((w) => { if (w.branch !== undefined) S.branches[w.path] = w.branch || ""; });
  if (!S.workspace && recent.length) S.workspace = recent[0];
  if (S.view === "session" && !S.chatId) renderStart();
  renderSessions();
}
async function chooseWorkspace(path) {
  path = path.trim();
  if (!path) return;
  try {
    const r = await api("/api/workspaces/check", { method: "POST", body: { path } });
    if (!r.ok) return toast(r.error || "That folder doesn’t exist", "error");
    S.workspace = { path: r.path, name: r.name, branch: r.branch };
    S.branches[r.path] = r.branch || "";
  } catch (e) { return toast(e.message, "error"); }
  if (!S.recentWs.some((w) => w.path === S.workspace.path)) S.recentWs.unshift(S.workspace);
  if (S.chatId && S.chat && S.chat.mode === "code") {  // move this session to the chosen project
    S.chat.workspace = S.workspace.path; setTop(S.chat);
    api(`/api/chats/${S.chatId}`, { method: "PATCH", body: { workspace: S.workspace.path } }).catch(() => {});
    const c = S.chats.find((x) => x.id === S.chatId); if (c) c.workspace = S.workspace.path;
  } else if (S.mode !== "code") { S.mode = "code"; syncMode(); }
  if (!S.chatId) renderStart();
  syncTray(); renderSessions(); input.focus();
}
async function pickFolder() {
  closeMenus();
  try {
    const r = await api("/api/workspaces/pick", { method: "POST" });
    if (r && r.path) chooseWorkspace(r.path);
  } catch (er) { toast(er.status === 404 ? "The folder picker isn’t available here. Type the path instead." : er.message, "error"); }
}
function projectMenu(btn) {
  const menu = $("proj-menu");
  if (!toggleMenu(btn.id === "start-proj" ? $("proj-btn") : btn, menu)) return;
  const cur = S.chatId ? S.chat && S.chat.workspace : S.workspace && S.workspace.path;
  menu.innerHTML = `<div class="mh">Projects</div>` +
    (S.recentWs.length ? S.recentWs.map((w) => `<button role="menuitemradio" data-ws="${esc(w.path)}" aria-checked="${w.path === cur}"><b>${esc(w.name || base(w.path))}</b><span class="mono">${esc(tilde(w.path))}</span></button>`).join("") : `<div class="mh">No projects yet</div>`) +
    `<hr><button class="row-btn" data-pick>${icon("folder")}Open folder…</button>
     <form id="ws-form"><input id="ws-path" placeholder="/Users/you/project" aria-label="Project folder path"><button class="btn sm" type="submit">Use</button></form>`;
}
$("proj-btn").onclick = () => projectMenu($("proj-btn"));
$("proj-menu").addEventListener("click", (e) => {
  const w = e.target.closest("[data-ws]");
  if (w) { closeMenus(); chooseWorkspace(w.dataset.ws); return; }
  if (e.target.closest("[data-pick]")) pickFolder();
});
$("proj-menu").addEventListener("submit", (e) => { e.preventDefault(); closeMenus(); chooseWorkspace($("ws-path").value); });
$("start").addEventListener("click", (e) => { if (e.target.closest("#start-proj")) { e.stopPropagation(); projectMenu($("start-proj")); } });
$("suggest").addEventListener("click", (e) => {
  const s = e.target.closest("[data-suggest]");
  if (s) { input.value = s.dataset.suggest; autosize(); input.focus(); }
});
$("voice-btn").onclick = () => voiceAgent(!(S.prefs.voice_mode));

async function voiceAgent(on) {
  try {
    const r = await api("/api/voice-agent", { method: "POST", body: { on } });
    S.prefs.voice_mode = !!r.voice_mode; applyPrefs({});
    toast(on ? "Voice agent is on. Tap your talk key (right Shift) and just talk; tap it again to stop." : "Voice agent stopped");
  } catch (e) { toast(e.message, "error"); }
  syncTray();
  if (S.view === "settings" && window.PAGES) window.PAGES.settings();
}
$("voice-pill").onclick = () => voiceAgent(false);

/* ================= mode switch ================= */
function syncMode() {
  document.querySelectorAll("#mode-seg [data-mode]").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.mode === S.mode)));
  input.placeholder = S.mode === "code" ? "Ask Monday anything, @ to add files" : "Ask Monday to do something on your Mac";
  syncTray();
}
async function setMode(mode) {
  if (S.mode === mode) return;
  S.mode = mode; syncMode();
  if (!S.chatId) { renderStart(); return; }
  if (mode === "code" && !(S.chat && S.chat.workspace)) {
    const ws = S.workspace || S.recentWs[0];
    if (!ws) { toast("Choose a project folder first.", "error"); S.mode = "computer"; syncMode(); return; }
    S.chat.workspace = ws.path;
  }
  S.chat.mode = mode;
  setTop(S.chat);
  api(`/api/chats/${S.chatId}`, { method: "PATCH", body: { mode, ...(mode === "code" ? { workspace: S.chat.workspace } : {}) } }).catch(() => {});
  const c = S.chats.find((x) => x.id === S.chatId); if (c) { c.mode = mode; c.workspace = S.chat.workspace; renderSessions(); }
}
$("mode-seg").addEventListener("click", (e) => { const b = e.target.closest("[data-mode]"); if (b) setMode(b.dataset.mode); });

/* ================= opening a session ================= */
async function openChat(id) {
  if (S.chatId === id && S.chat && !$("thread").hidden) return;
  S.chatId = id;
  $("start").hidden = true; $("thread").hidden = false; $("view-session").classList.remove("is-start");
  renderSessions();
  let chat;
  try { chat = await api(`/api/chats/${id}`); } catch (e) {
    $("thread").innerHTML = `<div class="errline">${icon("x")}This session couldn’t be loaded (${esc(e.message)}).</div>`; return;
  }
  if (S.chatId !== id) return;
  renderChat(chat);
}
function renderChat(chat) {
  S.chat = chat;
  S.mode = chat.mode || "computer";
  if (chat.mode === "code" && chat.workspace && !String(chat.id).startsWith("demo-")) S.workspace = { path: chat.workspace, name: base(chat.workspace) };
  syncMode(); setTop(chat);
  const th = $("thread");
  th.innerHTML = "";
  S.ctxUsed = 0;
  for (const m of chat.messages || []) {
    if (m.role === "user") th.appendChild(userTurn(m));
    else if (m.role === "assistant") {
      const t = new Turn(chat.id, m, false);
      th.appendChild(t.el);
      if (m.stats) S.ctxUsed = (m.stats.prompt_tokens || 0) + (m.stats.output_tokens || 0) || S.ctxUsed;
    }
  }
  let live = S.live[chat.id];
  if (!live && S.running.has(chat.id)) { live = S.live[chat.id] = new Turn(chat.id, {}, true); }
  if (live) th.appendChild(live.el);
  Object.values(S.approvals).filter((a) => a.chat_id === chat.id).forEach((a) => live && live.addApproval(a));
  updateCtx();
  scrollDown(true);
  setSending();
}
function userTurn(m) {
  const d = document.createElement("div");
  d.className = "turn user";
  const atts = (m.attachments || []).map((a) => a.kind === "image" ? `<img src="/api/uploads/${esc(a.id)}" alt="${esc(a.name)}">` : `<span class="chipfile">${icon("file")}${esc(a.name)}</span>`).join("");
  d.innerHTML = `<div class="ubox">${m.source === "voice" ? `<span class="voice-tag">Spoken to the voice agent</span>` : ""}${atts ? `<div class="uatt">${atts}</div>` : ""}${esc(m.content)}</div>`;
  return d;
}
function nearBottom() { const s = $("scroller"); return s.scrollHeight - s.scrollTop - s.clientHeight < 140; }
function scrollDown(force) { const s = $("scroller"); if (force || nearBottom()) requestAnimationFrame(() => (s.scrollTop = s.scrollHeight)); }

/* ================= tool rows ================= */
const P = (a) => a.path || a.file || a.name || "";
const LABELS = {
  list_files: (a) => ["List", `${P(a) || "."}${a.pattern ? `  ${a.pattern}` : ""}`],
  search_code: (a) => ["Search", `“${a.pattern || a.query || ""}”${P(a) && P(a) !== "." ? ` in ${P(a)}` : ""}${a.glob ? ` (${a.glob})` : ""}`],
  read_file: (a) => ["Read", `${P(a)}${a.start > 1 ? `  from line ${a.start}` : ""}`],
  write_file: (a) => ["Write", P(a)], edit_file: (a) => ["Edit", P(a)],
  run_command: (a) => ["Run", `$ ${a.command || a.cmd || ""}`],
  computer_use: (a) => ["Use computer", a.goal || ""], use_skill: (a) => ["Load skill", a.name || ""],
  open_app: (a) => ["Open", a.name], quit_app: (a) => ["Quit", a.name], open_url: (a) => ["Open", a.url], google: (a) => ["Google", a.query],
  youtube: (a) => ["YouTube", a.query], weather: (a) => ["Weather", a.place || "here"], web_search: (a) => ["Search web", `“${a.query || ""}”`],
  read_webpage: (a) => ["Read page", a.url], news: (a) => ["News", a.topic || "top stories"], find_files: (a) => ["Find files", `“${a.query || ""}”`],
  open_file: (a) => ["Open", a.name || a.path], move_to_trash: (a) => ["Move to Trash", a.name], battery: () => ["Battery", ""],
  volume: (a) => ["Volume", String(a.level ?? "")], media: (a) => ["Media", a.action], take_screenshot: () => ["Screenshot", ""],
  lock_screen: () => ["Lock screen", ""], read_screen: (a) => ["Look at the screen", a.question], add_reminder: (a) => ["Reminder", a.text],
  add_note: (a) => ["Note", a.title], add_event: (a) => ["Event", a.title], calendar: (a) => ["Calendar", a.day_offset ? "tomorrow" : "today"],
  set_timer: (a) => ["Timer", a.seconds ? `${Math.round(a.seconds / 60) || a.seconds + "s"}${a.seconds >= 60 ? " min" : ""}` : ""],
  remove_background: (a) => ["Remove background", a.path || "newest image"], study_notes: (a) => ["Study notes", a.query], scrape: (a) => ["Scrape", a.target || "this page"],
  look: (a) => ["Camera", a.question], sketch: (a) => ["Draw", a.subject], summarize_file: (a) => ["Summarize", a.name],
  click_element: (a) => ["Click", `#${a.id}`], type_into: (a) => ["Type", `“${a.text || ""}” into #${a.id}`], click: (a) => ["Click", `${a.x}, ${a.y}`],
  type_text: (a) => ["Type", `“${a.text || ""}”`], key: (a) => ["Press", a.combo], scroll: (a) => ["Scroll", a.direction], wait: (a) => ["Wait", a.seconds ? `${a.seconds}s` : ""],
  done: (a) => ["Done", a.summary],
};
// steps whose detail is code-like (a path, a command, a URL, a key) show it in monospace; the rest read as prose
const CODE_OBJ = new Set(["list_files", "search_code", "read_file", "write_file", "edit_file", "run_command", "open_url",
  "read_webpage", "open_file", "key", "click", "click_element"]);
function label(tool, args) {
  const f = LABELS[tool];
  if (f) { const [v, o] = f(args || {}); return [v, o == null ? "" : String(o)]; }
  const first = Object.values(args || {}).find((x) => typeof x === "string") || "";
  return [tool.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase()), first];
}
function extractDiff(step) {
  if (step.diff) return step.diff;
  const m = /```diff\n([\s\S]*?)```/.exec(step.result || "");
  return m ? m[1] : "";
}
function parseDiff(text) {
  const rows = []; let add = 0, del = 0, o = 0, n = 0, path = "";
  for (const line of String(text).replace(/\r/g, "").split("\n")) {
    if (line.startsWith("+++ ")) { path = line.slice(4).replace(/^b\//, ""); continue; }
    if (line.startsWith("--- ") || line.startsWith("diff ") || line.startsWith("index ")) continue;
    const h = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$/.exec(line);
    if (h) { o = +h[1]; n = +h[2]; rows.push({ t: "hunk", s: line }); continue; }
    if (line.startsWith("\\")) continue;
    if (line.startsWith("+")) { rows.push({ t: "add", n: n++, s: line.slice(1) }); add++; }
    else if (line.startsWith("-")) { rows.push({ t: "del", n: o++, s: line.slice(1) }); del++; }
    else if (rows.length) { rows.push({ t: "same", n: n, s: line.slice(1) }); o++; n++; }
  }
  while (rows.length && rows[rows.length - 1].t === "same" && !rows[rows.length - 1].s) rows.pop();
  return { rows, add, del, path };
}
function renderDiff(text, path) {
  const d = parseDiff(text);
  if (!d.rows.length) return `<pre class="out">${esc(text)}</pre>`;
  const sign = { add: "+", del: "−", same: " " };
  return `<div class="diff"><div class="dh">${icon("file")}<span class="mono">${esc(path || d.path)}</span><span class="grow"></span><span class="plus">+${d.add}</span><span class="minus">−${d.del}</span></div>
    <div class="dscroll"><table>${d.rows.map((r) => r.t === "hunk" ? `<tr class="hunk"><td colspan="3">${esc(r.s)}</td></tr>`
      : `<tr class="${r.t}"><td class="n">${r.n}</td><td class="s">${sign[r.t]}</td><td>${esc(r.s)}</td></tr>`).join("")}</table></div></div>`;
}
function tail(text, lines) {
  const all = String(text || "").split("\n");
  return all.length > lines ? { text: all.slice(-lines).join("\n"), cut: all.length - lines } : { text: all.join("\n"), cut: 0 };
}

/* ================= a turn (one assistant reply) ================= */
class Turn {
  constructor(chatId, msg, live) {
    this.chatId = chatId; this.live = live;
    this.d = { plan: msg.plan || [], steps: (msg.steps || []).map((s) => ({ ...s })), text: msg.content || "", thinking: msg.thinking || "",
      stats: msg.stats || {}, model: msg.model || "", error: "", stopped: false, cur: "", tokens: 0, splan: {} };
    this.t0 = Date.now(); this.raf = 0; this.approvals = {};
    this.el = document.createElement("div");
    this.el.className = "turn asst";
    this.el.innerHTML = `<div class="think" hidden><button type="button" aria-expanded="false">${icon("chev-r")}<span class="tl">Thinking</span></button><div class="tb" hidden></div></div>
      <div class="todos" hidden></div><div class="acts"></div><div class="perms"></div><div class="prose"></div><div class="errs"></div>
      <div class="status" hidden><span class="spin" aria-hidden="true">✻</span><span class="w">Working…</span><span class="tm"></span><span class="cur"></span></div><div class="foot" hidden></div>`;
    this.q = (s) => this.el.querySelector(s);
    this.q(".think > button").onclick = () => { const o = !this.q(".think").classList.contains("open"); this.q(".think").classList.toggle("open", o); this.q(".think .tb").hidden = !o; this.q(".think > button").setAttribute("aria-expanded", String(o)); };
    this.q(".acts").addEventListener("click", (e) => this.onActClick(e));
    this.q(".perms").addEventListener("click", (e) => { const b = e.target.closest("[data-ans]"); if (b) answerApproval(b.closest(".perm").dataset.id, b.dataset.ans); });
    this.render();
  }
  render() {
    this.renderPlan(); this.d.steps.forEach((s) => this.upsertStep(s)); this.renderThinking(); this.renderText(!this.live); this.renderErrs(); this.renderStatus(); this.renderFoot();
  }
  // plan
  setPlan(items) { this.d.plan = items || []; this.renderPlan(); }
  renderPlan() {
    const p = this.d.plan, box = this.q(".todos");
    box.hidden = !(p && p.length > 1);
    if (box.hidden) return;
    const now = this.live ? p.findIndex((x) => x.status === "todo" && x.tool !== "respond") : -1;
    const done = p.filter((x) => x.status === "done").length;
    box.innerHTML = `<h4>Plan<span class="muted">${done}/${p.length}</span></h4>` + p.map((x, i) => {
      const cls = i === now ? "now" : x.status;
      const mark = x.status === "done" ? "☑" : x.status === "skipped" ? "☒" : i === now ? "◐" : "☐";
      return `<div class="todo ${cls}"><span class="box" aria-hidden="true">${mark}</span><span>${esc(x.task)}</span><span class="tool">${esc(x.tool)}</span></div>`;
    }).join("");
  }
  // steps
  step(id) { return this.d.steps.find((s) => s.id === id); }
  addStep(s) { const st = { status: "running", ...s }; this.d.steps.push(st); this.upsertStep(st); this.d.cur = label(st.tool, st.args).join(" "); this.renderStatus(); }
  updateStep(id, patch) { const s = this.step(id); if (!s) return; Object.assign(s, patch); this.upsertStep(s); }
  upsertStep(s) {
    const box = this.q(".acts");
    let el = box.querySelector(`.act[data-id="${CSS.escape(String(s.id))}"]`);
    const fresh = !el;
    if (fresh) { el = document.createElement("div"); el.className = "act"; el.dataset.id = s.id; box.appendChild(el); }
    if (s._open === undefined && s.status !== "running") s._open = !!extractDiff(s) || ["error", "denied", "blocked"].includes(s.status);
    if (s.tool === "computer_use" && s._open === undefined) s._open = true;
    const [verb, obj] = label(s.tool, s.args);
    const diff = extractDiff(s);
    let meta = "";
    if (diff) { const d = parseDiff(diff); meta += `<span class="plus">+${d.add}</span><span class="minus">−${d.del}</span>`; }
    if (s.tool === "run_command" && s.exit_code !== undefined && s.exit_code !== null) meta += `<span>exit ${esc(s.exit_code)}</span>`;
    if (s.status === "denied") meta += "<span>denied</span>"; else if (s.status === "blocked") meta += "<span>blocked</span>"; else if (s.status === "skip") meta += "<span>skipped</span>";
    if (s.ms) meta += `<span class="ms">${s.ms >= 1000 ? (s.ms / 1000).toFixed(1) + "s" : s.ms + "ms"}</span>`;
    if (s.repaired) meta += `<span title="The first reply was malformed and Monday repaired it">repaired</span>`;
    const open = !!s._open;
    el.classList.toggle("open", open);
    el.innerHTML = `<button class="row" type="button" aria-expanded="${open}"><span class="sd st-${esc(s.status)}" aria-label="${esc(s.status)}"></span><span class="verb">${esc(verb)}</span><span class="obj${CODE_OBJ.has(s.tool) ? " code" : ""}">${esc(obj)}</span><span class="meta">${meta}${icon("chev-r", "chev")}</span></button>
      ${open ? `<div class="body">${this.body(s, diff)}</div>` : ""}`;
    if (this.live) scrollDown();
  }
  body(s, diff) {
    const parts = [];
    if (s.thought) parts.push(`<div class="thought">${esc(s.thought)}</div>`);
    if (s.tool === "computer_use") {
      const sp = this.d.splan[s.id];
      parts.push(`<div class="subs">${sp && sp.length ? `<div class="splan">On screen: ${sp.map(esc).join(" → ")}</div>` : ""}${(s.subs || []).map((x) => {
        const [v, o] = label(x.tool, x.args);
        const st = x.result === undefined ? "running" : /^error|I won't|didn't do it/.test(x.result || "") ? "error" : "ok";
        return `<div class="act"><div class="row"><span class="sd st-${st}"></span><span class="verb">${esc(v)}</span><span class="obj">${esc(o)}</span></div></div>`;
      }).join("")}</div>`);
      if (s.result) parts.push(`<pre class="out">${esc(s.result)}</pre>`);
      return parts.join("");
    }
    if (diff) { parts.push(renderDiff(diff, s.path || P(s.args || {}))); return parts.join(""); }
    if (s.status === "running") return parts.join("") || `<div class="thought">Running…</div>`;
    if (s.tool === "run_command") {
      const full = s._full, t = tail(s.output || s.result || "", full ? 100000 : 40);
      const ex = s.exit_code;
      parts.push(`<pre class="out"><span class="cmdline">$ ${esc((s.args || {}).command || "")}</span>${esc(t.text) || '<span class="muted">(no output)</span>'}${ex !== undefined && ex !== null ? `<span class="exit ${+ex === 0 ? "ok" : "bad"}">exit ${esc(ex)}</span>` : ""}</pre>`);
      if (t.cut) parts.push(`<button class="more-btn" data-full="${esc(s.id)}">Show ${t.cut} earlier lines</button>`);
      return parts.join("");
    }
    const res = String(s.result || "");
    if (!res) { parts.push(`<div class="thought">No output.</div>`); return parts.join(""); }
    const lim = s._full ? Infinity : 2400;
    parts.push(`<pre class="out">${esc(res.length > lim ? res.slice(0, lim) + "\n…" : res)}</pre>`);
    if (res.length > lim) parts.push(`<button class="more-btn" data-full="${esc(s.id)}">Show all (${Math.round(res.length / 1000)}k characters)</button>`);
    return parts.join("");
  }
  onActClick(e) {
    const full = e.target.closest("[data-full]");
    if (full) { const s = this.step(full.dataset.full); if (s) { s._full = true; this.upsertStep(s); } return; }
    const row = e.target.closest("button.row");
    if (!row) return;
    const el = row.closest(".act"), s = this.step(el.dataset.id);
    if (!s) return;
    s._open = !s._open; this.upsertStep(s);
  }
  screen(kind, d) {
    const s = [...this.d.steps].reverse().find((x) => x.tool === "computer_use");
    if (!s) return;
    if (kind === "screen_plan") this.d.splan[s.id] = d.steps || [];
    else {
      s.subs = s.subs || [];
      if (kind === "screen_action") s.subs.push({ step: d.step, tool: d.tool, args: d.args, thought: d.thought });
      else { const x = [...s.subs].reverse().find((y) => y.step === d.step) || s.subs[s.subs.length - 1]; if (x) x.result = d.result || ""; }
      if (kind === "screen_action") { this.d.cur = label(d.tool, d.args).join(" "); this.renderStatus(); }
    }
    s._open = true; this.upsertStep(s);
  }
  // text
  addText(t) { this.d.text += t; this.d.cur = "Writing the answer"; this.schedule(); }
  schedule() { if (this.raf) return; this.raf = requestAnimationFrame(() => { this.raf = 0; this.renderText(false); scrollDown(); }); }
  renderText(final) {
    const p = this.q(".prose");
    p.innerHTML = this.d.text ? MD.render(this.d.text, this.live && !final) : "";
    p.hidden = !this.d.text;
  }
  addThinking(t) { this.d.thinking += t; this.renderThinking(); }
  renderThinking() {
    const box = this.q(".think");
    box.hidden = !this.d.thinking;
    if (box.hidden) return;
    this.q(".think .tb").textContent = this.d.thinking;
    this.q(".think .tl").textContent = this.live && !this.d.text ? "Thinking…" : "Thought process";
  }
  error(text) { this.d.error = text; this.renderErrs(); }
  renderErrs() {
    const e = this.q(".errs");
    e.innerHTML = (this.d.error ? `<div class="errline">${icon("x")}<span>${esc(this.d.error)}</span></div>` : "") + (this.d.stopped ? `<div class="stopped">Interrupted.</div>` : "");
  }
  progress(text) { this.d.cur = text; this.renderStatus(); }
  tokens(n) { this.d.tokens += n || 0; }
  renderStatus() {
    const st = this.q(".status");
    st.hidden = !this.live;
    if (st.hidden) return;
    const secs = Math.round((Date.now() - this.t0) / 1000);
    const pending = Object.keys(this.approvals).length;
    st.querySelector(".w").textContent = pending ? "Waiting for your permission…" : "Working…";
    st.querySelector(".tm").textContent = `${secs}s${this.d.tokens ? ` · ${kfmt(this.d.tokens)} tokens` : ""} · Esc to interrupt`;
    st.querySelector(".cur").textContent = this.d.cur ? `— ${this.d.cur}` : "";
  }
  renderFoot() {
    const f = this.q(".foot");
    f.hidden = this.live || !(this.d.text || this.d.steps.length);
    if (f.hidden) return;
    const st = this.d.stats || {};
    const meta = [prettyModel(this.d.model), st.route && st.route !== "harness" ? st.route : "", st.seconds ? `${st.seconds}s` : "", st.output_tokens ? `${kfmt(st.output_tokens)} tokens` : ""].filter(Boolean).join(" · ");
    f.innerHTML = `<button class="icon-btn" data-copy-answer aria-label="Copy answer" data-tip="Copy">${icon("copy")}</button><span class="fm">${esc(meta)}</span>`;
    f.querySelector("[data-copy-answer]").onclick = () => copy(this.d.text);
  }
  // permission prompts
  addApproval(a) {
    if (this.approvals[a.id]) return;
    this.approvals[a.id] = a;
    const args = a.args || {};
    let title, pv = "";
    if (a.tool === "edit_file" || a.tool === "write_file") {
      title = `Monday wants to ${a.tool === "write_file" ? "write" : "edit"} <code>${esc(P(args))}</code>`;
      pv = a.preview ? renderDiff(a.preview, P(args)) : "";
    } else if (a.tool === "run_command") {
      title = "Monday wants to run a command";
      pv = `<pre class="out"><span class="cmdline">$ ${esc(String(a.preview || args.command || "").replace(/^\$\s*/, ""))}</span></pre>`;
    } else {
      const [v, o] = label(a.tool, args);
      title = `Monday wants to: ${esc(v)} <code>${esc(o)}</code>`;
      pv = a.preview ? `<pre class="out">${esc(a.preview)}</pre>` : "";
    }
    const d = document.createElement("div");
    d.className = "perm"; d.dataset.id = a.id; d.setAttribute("role", "group"); d.setAttribute("aria-label", "Permission request");
    d.innerHTML = `<h5>${title}</h5>${a.reason ? `<p class="why">${esc(a.reason)}</p>` : ""}<div class="pv">${pv}</div>
      <div class="opts"><button class="primary" data-ans="once"><kbd>1</kbd>Allow once</button><button data-ans="always"><kbd>2</kbd>Always allow in this session</button><button data-ans="deny"><kbd>3</kbd>Deny</button></div>`;
    this.q(".perms").appendChild(d);
    this.renderStatus();
    if (S.chatId === this.chatId) { scrollDown(true); setTimeout(() => d.querySelector(".primary").focus({ preventScroll: true }), 50); }
  }
  resolveApproval(id, how) {
    const d = this.q(`.perm[data-id="${CSS.escape(id)}"]`);
    delete this.approvals[id];
    if (d) {
      d.classList.add("done");
      const h = d.querySelector("h5");
      h.insertAdjacentHTML("beforeend", ` <span class="muted" style="font-weight:400">— ${how === "deny" || how === false ? "denied" : how === "always" ? "always allowed" : "allowed"}</span>`);
      setTimeout(() => d.remove(), 2500);
    }
    this.renderStatus();
  }
  finish(msg) {
    this.live = false;
    if (msg) {
      const keep = Object.fromEntries(this.d.steps.map((s) => [s.id, s]));
      this.d.steps = (msg.steps || this.d.steps).map((s) => ({ ...(keep[s.id] || {}), ...s, status: s.status || keep[s.id]?.status || "ok" }));
      this.d.plan = msg.plan && msg.plan.length ? msg.plan : this.d.plan;
      this.d.text = msg.content != null && msg.content !== "" ? msg.content : this.d.text;
      this.d.thinking = msg.thinking || this.d.thinking; this.d.stats = msg.stats || {}; this.d.model = msg.model || this.d.model;
    }
    this.d.steps.forEach((s) => { if (s.status === "running") s.status = this.d.stopped ? "skip" : "ok"; });
    Object.keys(this.approvals).forEach((id) => this.resolveApproval(id, false));
    this.q(".acts").innerHTML = "";
    this.render();
  }
}

/* ================= live events ================= */
function liveTurn(chatId, create) {
  let t = S.live[chatId];
  if (!t && create) {
    t = S.live[chatId] = new Turn(chatId, {}, true);
    if (S.chatId === chatId && !$("thread").hidden) { $("thread").appendChild(t.el); scrollDown(true); }
  }
  return t;
}
function onChatCreated(m) {
  if (!S.chats.some((c) => c.id === m.id)) S.chats.unshift({ id: m.id, title: m.title, mode: m.mode || "computer", workspace: m.workspace || "", updated: Date.now() / 1000 });
  if (S.pendingNew) {
    S.pendingNew = false;
    S.chatId = m.id;
    S.chat = { id: m.id, title: m.title, mode: m.mode || S.mode, workspace: m.workspace || (S.workspace && S.workspace.path) || "", messages: [] };
    $("start").hidden = true; $("thread").hidden = false; $("thread").innerHTML = "";
    $("view-session").classList.remove("is-start");
    setTop(S.chat);
    history.replaceState(null, "", `#/s/${m.id}`);
  }
  renderSessions();
}
function onUserMessage(m) {
  if (m.chat_id === S.chatId && !$("thread").hidden) {
    $("thread").appendChild(userTurn(m.message)); scrollDown(true);
    if (S.chat) S.chat.messages.push(m.message);
  }
}
function onRun(m) {
  const id = m.chat_id;
  if (!id) return;
  if (m.kind === "start") {
    S.running.add(id); delete S.live[id]; liveTurn(id, true); renderSessions(); setSending(); return;
  }
  const t = liveTurn(id, m.kind !== "done");
  switch (m.kind) {
    case "plan": case "plan_update": t.setPlan(m.items); break;
    case "tool_call": t.addStep({ id: m.id, tool: m.tool, args: m.args, thought: m.thought, repaired: m.repaired }); break;
    case "tool_result": {
      const patch = { result: m.result, status: m.status, ms: m.ms };
      for (const k of ["diff", "path", "exit_code", "output"]) if (m[k] !== undefined) patch[k] = m[k];
      t.updateStep(m.id, patch); break;
    }
    case "screen_plan": case "screen_action": case "screen_result": t.screen(m.kind, m); break;
    case "progress": t.progress(m.text); break;
    case "repair": t.progress("Fixing a malformed tool call"); break;
    case "auto_approved": t.progress(`Auto-approved: ${m.reason || m.tool}`); break;
    case "model": t.tokens(m.tokens); if (m.prompt_tokens && id === S.chatId) { S.ctxUsed = m.prompt_tokens + (m.tokens || 0); updateCtx(); } break;
    case "thinking": t.addThinking(m.text); break;
    case "answer_start": t.progress("Writing the answer"); break;
    case "delta": t.addText(m.text); break;
    case "answer_end": t.tokens(m.output_tokens); if (id === S.chatId) { S.ctxUsed = (m.prompt_tokens || 0) + (m.output_tokens || 0); updateCtx(); } break;
    case "error": t.error(m.text); break;
    case "stopped": t.d.stopped = true; t.renderErrs(); break;
    case "done": {
      S.running.delete(id);
      if (t) t.finish(m.message); else if (id === S.chatId) openChat(id);
      delete S.live[id];
      if (S.chat && S.chat.id === id && m.message) S.chat.messages.push(m.message);
      const c = S.chats.find((x) => x.id === id);
      if (c) { c.updated = Date.now() / 1000; S.chats.sort((a, b) => b.updated - a.updated); }
      renderSessions(); setSending();
      break;
    }
    default: break;
  }
}
setInterval(() => { const t = S.chatId && S.live[S.chatId]; if (t) t.renderStatus(); }, 500);

function onApproval(a) {
  S.approvals[a.id] = a;
  const t = liveTurn(a.chat_id, true);
  t.addApproval(a);
  if (a.chat_id !== S.chatId) {
    const c = S.chats.find((x) => x.id === a.chat_id);
    toast(`Monday needs your permission in “${c ? c.title : "another session"}”`);
  }
}
function onApprovalDone(m) {
  const a = S.approvals[m.id];
  delete S.approvals[m.id];
  if (a) { const t = S.live[a.chat_id]; if (t) t.resolveApproval(m.id, m.allow ? (a._how || "once") : "deny"); }
}
function answerApproval(id, how) {
  const a = S.approvals[id];
  if (!a) return;
  a._how = how;
  send({ type: "approve", id, allow: how !== "deny", always: how === "always" });
}
function pendingApproval() {
  return Object.values(S.approvals).filter((a) => a.chat_id === S.chatId).pop();
}

/* ================= composer ================= */
function autosize() { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, window.innerHeight * 0.4) + "px"; setSending(); }
input.addEventListener("input", () => { autosize(); mentionCheck(); });
function isRunning() { return !!(S.chatId && S.running.has(S.chatId)); }
function setSending() {
  const b = $("send-btn"), running = isRunning(), empty = !input.value.trim() && !S.attachments.length;
  const stop = running && empty;
  b.classList.toggle("stop", stop);
  b.innerHTML = stop ? icon("stop") : icon("up");
  b.setAttribute("aria-label", stop ? "Stop" : "Send");
  b.disabled = !stop && (empty || S.attachments.some((a) => a.loading));
}
$("composer").addEventListener("submit", (e) => { e.preventDefault(); submit(); });
function submit() {
  if (isRunning() && !input.value.trim() && !S.attachments.length) return stopRun();
  const text = input.value.trim();
  if (!text && !S.attachments.length) return;
  if (!S.chatId && S.mode === "code" && !S.workspace) { toast("Choose a project folder first.", "error"); projectMenu($("proj-btn")); return; }
  const mode = S.chatId ? (S.chat && S.chat.mode) || S.mode : S.mode;
  const workspace = mode === "code" ? (S.chat && S.chat.workspace) || (S.workspace && S.workspace.path) : undefined;
  const options = { mode, approve: S.prefs.approve, search: S.prefs.search !== false, think: !!S.prefs.think };
  if (workspace) options.workspace = workspace;
  if (!S.chatId) S.pendingNew = true;
  send({ type: "send", chat_id: S.chatId, text, attachments: S.attachments.filter((a) => a.id).map((a) => a.id), options });
  input.value = ""; S.attachments = []; renderAtts(); autosize(); closeMention();
}
function stopRun() { send({ type: "cancel" }); const t = S.live[S.chatId]; if (t) t.progress("Stopping…"); }
input.addEventListener("keydown", (e) => {
  if (mention.open && mentionKey(e)) return;
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(); }
});

/* hold the mic to dictate: the words land in the message box (Whisper on this Mac) */
let dictating = false;
function dictate(on) {
  if (on === dictating) return;
  dictating = on; $("mic-btn").classList.toggle("rec", on);
  send({ type: "ptt", down: on, chat_id: S.chatId });
}
$("mic-btn").addEventListener("pointerdown", (e) => { e.preventDefault(); $("mic-btn").setPointerCapture(e.pointerId); dictate(true); });
$("mic-btn").addEventListener("pointerup", () => dictate(false));
$("mic-btn").addEventListener("pointercancel", () => dictate(false));

/* attachments */
$("attach-btn").onclick = () => $("file-input").click();
$("file-input").onchange = (e) => { [...e.target.files].forEach(upload); e.target.value = ""; };
document.addEventListener("paste", (e) => { if (document.activeElement !== input) return; const f = [...(e.clipboardData?.files || [])]; if (f.length) { e.preventDefault(); f.forEach(upload); } });
$("composer").addEventListener("dragover", (e) => e.preventDefault());
$("composer").addEventListener("drop", (e) => { e.preventDefault(); [...e.dataTransfer.files].forEach(upload); });
async function upload(file) {
  const a = { name: file.name, kind: file.type.startsWith("image/") ? "image" : "file", loading: true, url: file.type.startsWith("image/") ? URL.createObjectURL(file) : "" };
  S.attachments.push(a); renderAtts(); setSending();
  const fd = new FormData(); fd.append("file", file);
  try { Object.assign(a, await api("/api/upload", { method: "POST", form: fd }), { loading: false }); }
  catch (e) { S.attachments = S.attachments.filter((x) => x !== a); toast(`Couldn’t attach ${file.name}: ${e.message}`, "error"); }
  renderAtts(); setSending();
}
function renderAtts() {
  const box = $("atts");
  box.hidden = !S.attachments.length;
  box.innerHTML = S.attachments.map((a, i) => `<span class="att ${a.loading ? "loading" : ""}">${a.kind === "image" ? `<img src="${esc(a.url || `/api/uploads/${a.id}`)}" alt="">` : icon("file")}<span class="n">${esc(a.name)}</span><button type="button" data-rm="${i}" aria-label="Remove ${esc(a.name)}">${icon("x")}</button></span>`).join("");
}
$("atts").onclick = (e) => { const b = e.target.closest("[data-rm]"); if (b) { S.attachments.splice(+b.dataset.rm, 1); renderAtts(); setSending(); } };

/* @-mentions (Code mode) */
const mention = { open: false, items: [], idx: 0, start: 0, timer: 0 };
function mentionCheck() {
  const mode = S.chatId ? S.chat && S.chat.mode : S.mode;
  const ws = S.chatId ? S.chat && S.chat.workspace : S.workspace && S.workspace.path;
  if (mode !== "code" || !ws) return closeMention();
  const pos = input.selectionStart, before = input.value.slice(0, pos);
  const m = /(^|\s)@([\w./-]*)$/.exec(before);
  if (!m) return closeMention();
  mention.start = pos - m[2].length - 1;
  clearTimeout(mention.timer);
  mention.timer = setTimeout(async () => {
    let files = [];
    try { files = (await api(`/api/workspaces/files?path=${encodeURIComponent(ws)}&q=${encodeURIComponent(m[2])}`)).files || []; } catch (e) { return closeMention(); }
    if (!files.length) return closeMention();
    mention.items = files.slice(0, 12); mention.idx = 0; mention.open = true; renderMention();
  }, 120);
}
function renderMention() {
  const box = $("mention");
  box.hidden = !mention.open;
  box.innerHTML = `<div class="mh">Files</div>` + mention.items.map((f, i) => `<button type="button" role="option" class="${i === mention.idx ? "hl" : ""}" aria-selected="${i === mention.idx}" data-file="${esc(f)}">${icon("file")}${esc(f)}</button>`).join("");
}
function closeMention() { mention.open = false; $("mention").hidden = true; }
function pickMention(f) {
  const pos = input.selectionStart;
  input.value = input.value.slice(0, mention.start) + "@" + f + " " + input.value.slice(pos);
  const c = mention.start + f.length + 2; input.setSelectionRange(c, c);
  closeMention(); autosize(); input.focus();
}
function mentionKey(e) {
  if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); mention.idx = (mention.idx + (e.key === "ArrowDown" ? 1 : -1) + mention.items.length) % mention.items.length; renderMention(); return true; }
  if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); pickMention(mention.items[mention.idx]); return true; }
  if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeMention(); return true; }
  return false;
}
$("mention").addEventListener("mousedown", (e) => { const b = e.target.closest("[data-file]"); if (b) { e.preventDefault(); pickMention(b.dataset.file); } });

/* permission-mode menu */
function closeMenus() { document.querySelectorAll(".pop > .menu").forEach((m) => { m.hidden = true; m.previousElementSibling?.setAttribute("aria-expanded", "false"); }); }
function toggleMenu(btn, menu) {
  const open = menu.hidden;
  closeMenus(); closeFloating();
  menu.hidden = !open; btn.setAttribute("aria-expanded", String(open));
  if (open) setTimeout(() => menu.querySelector("button")?.focus(), 0);
  return open;
}
$("perm-btn").onclick = () => toggleMenu($("perm-btn"), $("perm-menu"));
$("perm-menu").onclick = (e) => { const b = e.target.closest("[data-approve]"); if (b) { closeMenus(); setPref("approve", b.dataset.approve); input.focus(); } };

/* model menu */
$("model-btn").onclick = async () => {
  const menu = $("model-menu");
  if (!toggleMenu($("model-btn"), menu)) return;
  menu.innerHTML = `<div class="mh">Loading models…</div>`;
  let m;
  try { m = await api("/api/models"); } catch (e) { menu.innerHTML = `<div class="mh">Couldn’t list models</div>`; return; }
  const item = (name, sub) => `<button role="menuitemradio" class="${name === m.selected ? "sel" : ""}" aria-checked="${name === m.selected}" data-model="${esc(name)}"><b>${esc(prettyModel(name))}</b><span>${esc(sub)}</span></button>`;
  let html = `<div class="mh">On this Mac</div>`;
  html += m.ollama ? (m.local.map((x) => item(x.name, [x.name, x.params, gb(x.size), x.fits === false ? "too big for this Mac" : "", x.tiny ? "too small for agents" : ""].filter(Boolean).join(" · "))).join("") || `<div class="mh">No local models yet</div>`) : `<div class="mh">Ollama isn’t running</div>`;
  if (m.openai && m.openai.models && m.openai.models.length) html += `<hr><div class="mh">OpenAI-compatible server</div>` + m.openai.models.map((x) => item(x.name, m.openai.url)).join("");
  const hosted = (m.aistudio || []).filter((x) => x.available);
  if (hosted.length) html += `<hr><div class="mh">Google AI Studio</div>` + hosted.map((x) => item(x.name, "hosted · needs internet")).join("");
  html += `<hr><button data-manage><b>Manage models…</b></button>`;
  menu.innerHTML = html;
};
$("model-menu").onclick = async (e) => {
  if (e.target.closest("[data-manage]")) { closeMenus(); location.hash = "#/models"; return; }
  const b = e.target.closest("[data-model]");
  if (!b) return;
  closeMenus();
  $("model-dot").className = "dot wait";
  try { applyModel(await api("/api/models/select", { method: "POST", body: { model: b.dataset.model } })); toast(`Using ${prettyModel(b.dataset.model)}`); }
  catch (er) { toast(er.message, "error"); applyModel(S.model); }
};

document.addEventListener("click", (e) => {
  if (!e.target.closest(".pop")) closeMenus();
  if (floating && !floating.el.contains(e.target) && !e.target.closest("[data-more]")) closeFloating();
  if (!e.target.closest("#mention") && e.target !== input) closeMention();
  const c = e.target.closest("[data-copy]");
  if (c) { const code = c.parentElement.querySelector("code"); if (code) copy(code.textContent); }
});

/* ================= keyboard ================= */
document.addEventListener("keydown", (e) => {
  const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName);
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); location.hash = "#/"; if (location.hash === "#/") startScreen(); return; }
  if (e.key === "Escape") {
    if (floating) return closeFloating();
    if (document.querySelector(".pop > .menu:not([hidden])")) return closeMenus();
    if ($("app").classList.contains("drawer")) return closeDrawer();
    const a = pendingApproval();
    if (a) { e.preventDefault(); return answerApproval(a.id, "deny"); }
    if (isRunning()) { e.preventDefault(); return stopRun(); }
    return;
  }
  const a = pendingApproval();
  if (a && !typing && ["1", "2", "3"].includes(e.key)) { e.preventDefault(); answerApproval(a.id, { 1: "once", 2: "always", 3: "deny" }[e.key]); return; }
  if (e.key === "/" && !typing && S.view === "session") { e.preventDefault(); input.focus(); }
  const menu = document.querySelector(".pop > .menu:not([hidden]), body > .menu");
  if (menu && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
    const items = [...menu.querySelectorAll("button")];
    const i = items.indexOf(document.activeElement);
    e.preventDefault(); items[(i + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length]?.focus();
  }
});
window.addEventListener("hashchange", route);

/* ================= demo: a scripted session, for previews ================= */
const Demo = {
  timers: [], waiting: null,
  start(kind) {
    this.stop(); S.demo = kind;
    const id = "demo-" + kind;
    S.chatId = id; S.running.delete(id); delete S.live[id];
    const chat = kind === "code"
      ? { id, title: "Add a /health endpoint", mode: "code", workspace: "/Users/you/Developer/shop-api", messages: [] }
      : { id, title: "Battery, weather and a message", mode: "computer", workspace: "", messages: [] };
    S.branches[chat.workspace] = "main";
    $("start").hidden = true; $("thread").hidden = false; $("view-session").classList.remove("is-start");
    renderChat(chat);
    const text = kind === "code" ? "Add a /health endpoint that returns the app version, with a test." : "What's my battery and the weather in Karachi? Then open WhatsApp and send hi to Ali.";
    onUserMessage({ chat_id: id, message: { role: "user", content: text } });
    this.script = kind === "code" ? this.codeScript(id) : this.computerScript(id);
    this.next();
  },
  stop() { this.timers.forEach(clearTimeout); this.timers = []; this.waiting = null; },
  next() {
    const step = this.script.shift();
    if (!step) return;
    if (step.wait) { this.waiting = step; onApproval(step.wait); return; }
    this.timers.push(setTimeout(() => { step.ev.forEach((m) => (m.type ? onMessage(m) : onRun(m))); this.next(); }, step.ms));
  },
  intercept(msg) {
    if (msg.type === "approve" && this.waiting) {
      const w = this.waiting; this.waiting = null;
      onApprovalDone({ id: msg.id, allow: msg.allow });
      if (!msg.allow) {
        this.script = this.script.filter((s) => !s.ifAllowed);
        onRun({ chat_id: S.chatId, kind: "tool_result", id: w.step, status: "denied", result: "the user did not allow this action, so it was not done" });
      }
      this.next();
      return true;
    }
    if (msg.type === "cancel") { this.stop(); onRun({ chat_id: S.chatId, kind: "stopped" }); onRun({ chat_id: S.chatId, kind: "done", message: null }); return true; }
    if (msg.type === "send") { toast("This is a demo session. Start a new session to talk to Monday."); return true; }
    return false;
  },
  codeScript(id) {
    const r = (kind, d = {}) => ({ chat_id: id, kind, ...d });
    const diff = `--- a/src/shop/app.py\n+++ b/src/shop/app.py\n@@ -1,6 +1,7 @@\n from fastapi import FastAPI\n \n+from . import __version__\n from .routes import orders, products\n \n app = FastAPI(title="Shop API")\n@@ -9,3 +10,8 @@ app.include_router(products.router)\n app.include_router(orders.router)\n \n \n+@app.get("/health")\n+def health() -> dict:\n+    """Liveness probe for the load balancer."""\n+    return {"status": "ok", "version": __version__}\n`;
    const test = `--- /dev/null\n+++ b/tests/test_health.py\n@@ -0,0 +1,9 @@\n+from fastapi.testclient import TestClient\n+\n+from shop.app import app\n+\n+\n+def test_health_reports_version():\n+    r = TestClient(app).get("/health")\n+    assert r.status_code == 200\n+    assert r.json()["status"] == "ok"\n`;
    const plan = (s) => [["Look at how the app and its routes are laid out", "list_files"], ["Read the FastAPI app", "read_file"], ["Add the /health route", "edit_file"], ["Add a test for it", "write_file"], ["Run the tests", "run_command"], ["Summarise the change", "respond"]].map(([task, tool], i) => ({ task, tool, status: s[i] || "todo" }));
    return [
      { ms: 300, ev: [r("start"), r("model", { tokens: 64, prompt_tokens: 2100 }), r("plan", { items: plan([]) })] },
      { ms: 500, ev: [r("tool_call", { id: "s1", tool: "list_files", args: { path: "." }, thought: "Get the lay of the land first." })] },
      { ms: 700, ev: [r("tool_result", { id: "s1", status: "ok", ms: 12, result: "pyproject.toml\nREADME.md\nsrc/shop/__init__.py\nsrc/shop/app.py\nsrc/shop/routes/orders.py\nsrc/shop/routes/products.py\ntests/test_orders.py\ntests/test_products.py" }), r("plan_update", { items: plan(["done"]) })] },
      { ms: 400, ev: [r("tool_call", { id: "s2", tool: "search_code", args: { pattern: "__version__", path: "src" } })] },
      { ms: 500, ev: [r("tool_result", { id: "s2", status: "ok", ms: 30, result: "src/shop/__init__.py:1:__version__ = \"1.4.2\"" })] },
      { ms: 400, ev: [r("tool_call", { id: "s3", tool: "read_file", args: { path: "src/shop/app.py" } }), r("model", { tokens: 48 })] },
      { ms: 600, ev: [r("tool_result", { id: "s3", status: "ok", ms: 4, result: "1  from fastapi import FastAPI\n2  \n3  from .routes import orders, products\n4  \n5  app = FastAPI(title=\"Shop API\")\n6  \n7  \n8  app.include_router(products.router)\n9  app.include_router(orders.router)" }), r("plan_update", { items: plan(["done", "done"]) })] },
      { ms: 600, ev: [r("tool_call", { id: "s4", tool: "edit_file", args: { path: "src/shop/app.py", old: "app.include_router(orders.router)\n", new: "…" }, thought: "Import the version and add the route after the routers." }), r("model", { tokens: 160 })] },
      { step: "s4", wait: { type: "approval", id: "a1", chat_id: id, tool: "edit_file", args: { path: "src/shop/app.py" }, reason: "Edits a file in your project", preview: diff } },
      { ms: 300, ifAllowed: true, ev: [r("tool_result", { id: "s4", status: "ok", ms: 8, diff, path: "src/shop/app.py", result: "Edited src/shop/app.py (+6 −0)" }), r("plan_update", { items: plan(["done", "done", "done"]) })] },
      { ms: 500, ev: [r("tool_call", { id: "s5", tool: "write_file", args: { path: "tests/test_health.py", content: "…" } }), r("model", { tokens: 120 })] },
      { ms: 500, ev: [r("tool_result", { id: "s5", status: "ok", ms: 6, diff: test, path: "tests/test_health.py", result: "Created tests/test_health.py (+9)" }), r("plan_update", { items: plan(["done", "done", "done", "done"]) })] },
      { ms: 400, ev: [r("tool_call", { id: "s6", tool: "run_command", args: { command: "pytest -q" } })] },
      { step: "s6", ifAllowedNext: true, wait: { type: "approval", id: "a2", chat_id: id, tool: "run_command", args: { command: "pytest -q" }, reason: "Runs a command in your project folder", preview: "pytest -q" } },
      { ms: 1400, ifAllowed: true, ev: [r("tool_result", { id: "s6", status: "ok", ms: 1320, exit_code: 0, result: "........                                                                 [100%]\n8 passed in 0.41s" }), r("plan_update", { items: plan(["done", "done", "done", "done", "done"]) })] },
      { ms: 300, ev: [r("answer_start")] },
      ...["Added **`GET /health`** to `src/shop/app.py`. It returns ", "`{\"status\": \"ok\", \"version\": \"1.4.2\"}` using the package's ", "`__version__`, so it stays in sync with releases.\n\n", "- New test: `tests/test_health.py`\n- Test run: `pytest -q` → **8 passed**\n\n", "Want me to add it to the README's API section too?"].map((text) => ({ ms: 180, ev: [r("delta", { text })] })),
      { ms: 200, ev: [r("answer_end", { prompt_tokens: 5200, output_tokens: 96, seconds: 9.4 })] },
      { ms: 100, ev: [r("done", { message: { role: "assistant", content: null, model: "gemma4:e4b", stats: { seconds: 9.4, output_tokens: 488 }, plan: plan(["done", "done", "done", "done", "done", "done"]) } })] },
    ];
  },
  computerScript(id) {
    const r = (kind, d = {}) => ({ chat_id: id, kind, ...d });
    const plan = (s) => [["Get the battery level", "battery"], ["Get the weather in Karachi", "weather"], ["Open WhatsApp and send hi to Ali", "computer_use"]].map(([task, tool], i) => ({ task, tool, status: s[i] || "todo" }));
    return [
      { ms: 300, ev: [r("start"), r("plan", { items: plan([]) })] },
      { ms: 400, ev: [r("tool_call", { id: "s1", tool: "battery", args: {} })] },
      { ms: 400, ev: [r("tool_result", { id: "s1", status: "ok", ms: 40, result: "Battery is at 41 percent." }), r("plan_update", { items: plan(["done"]) })] },
      { ms: 400, ev: [r("tool_call", { id: "s2", tool: "weather", args: { place: "Karachi" } })] },
      { ms: 700, ev: [r("tool_result", { id: "s2", status: "ok", ms: 640, result: "In Karachi it's 29°C and sunny, with a high of 31 and a low of 25 today." }), r("plan_update", { items: plan(["done", "done"]) })] },
      { ms: 400, ev: [r("tool_call", { id: "s3", tool: "computer_use", args: { goal: "Open WhatsApp and send hi to Ali" } })] },
      { ms: 500, ev: [r("screen_plan", { steps: ["Open WhatsApp", "Search for Ali", "Type hi and press Enter"] })] },
      { ms: 600, ev: [r("screen_action", { step: 1, tool: "open_app", args: { name: "WhatsApp" } })] },
      { ms: 700, ev: [r("screen_result", { step: 1, tool: "open_app", result: "WhatsApp is open" }), r("screen_action", { step: 2, tool: "type_into", args: { id: 4, text: "Ali" } })] },
      { ms: 700, ev: [r("screen_result", { step: 2, tool: "type_into", result: "ok" }), r("screen_action", { step: 3, tool: "click_element", args: { id: 12 } })] },
      { ms: 600, ev: [r("screen_result", { step: 3, tool: "click_element", result: "ok" }), r("screen_action", { step: 4, tool: "type_into", args: { id: 7, text: "hi", press_enter: true } })] },
      { step: "s3", wait: { type: "approval", id: "a1", chat_id: id, tool: "type_into", args: { id: 7, text: "hi" }, reason: "submitting “Message Ali” sends a message" } },
      { ms: 600, ev: [r("screen_result", { step: 4, tool: "type_into", result: "ok" }), r("tool_result", { id: "s3", status: "ok", ms: 4100, result: "Done after 4 steps: Sent “hi” to Ali." }), r("plan_update", { items: plan(["done", "done", "done"]) }), r("answer_start")] },
      ...["Your battery is at **41%**. Karachi is **29°C and sunny** (high 31, low 25). ", "And I sent “hi” to Ali on WhatsApp!"].map((text) => ({ ms: 200, ev: [r("delta", { text })] })),
      { ms: 200, ev: [r("done", { message: { role: "assistant", content: null, model: "gemma4:e4b", stats: { seconds: 6.8, output_tokens: 140 } } })] },
    ];
  },
};

/* ================= boot ================= */
setTheme(theme());
connect();
loadChats().then(() => { route(); });
api("/api/status").then((s) => { if (s.hardware) S.hardware = s.hardware; }).catch(() => {});
window.addEventListener("resize", () => { if (window.innerWidth > 900) closeDrawer(); });
