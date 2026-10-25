"use strict";
/* Forge Studio pages: Models, Skills, Evals, Train and Settings. Uses the helpers in app.js. */

const page = (id, title, lead, body) => {
  $(id).innerHTML = `<div class="column"><div class="ph"><h1>${title}</h1>${lead ? `<p>${lead}</p>` : ""}</div>${body}</div>`;
};

/* ================= Models ================= */
async function pageModels() {
  const lead = "Open-weight models on this Mac, Gemma 4 builds to pull, any OpenAI-compatible server (llama.cpp, LM Studio, MLX) and hosted Gemma. The selected model powers every session.";
  if (!$("view-models").childElementCount) page("view-models", "Models", lead, `<p class="muted">Loading…</p>`);
  let m;
  try { m = await api("/api/models"); } catch (e) { page("view-models", "Models", lead, `<div class="note">Forge couldn’t list models (${esc(e.message)}).</div>`); return; }
  if (S.view !== "models") return;
  const g4 = (x) => x.family === "gemma4" || x.name.startsWith("gemma4");
  const use = (name) => name === m.selected ? `<span class="tag b">${icon("check")} In use</span>` : `<button class="btn sm" data-use="${esc(name)}">Use</button>`;
  const local = (x) => `<div class="card ${x.name === m.selected ? "sel" : ""}"><div><h3>${esc(prettyModel(x.name))}</h3><div class="sub">${esc(x.name)}</div></div>
      <div class="tags">${[x.params, x.quant, x.format === "safetensors" ? "MLX" : (x.format || "").toUpperCase(), gb(x.size)].filter(Boolean).map((p) => `<span class="tag">${esc(p)}</span>`).join("")}
        ${x.loaded ? '<span class="tag g">loaded</span>' : ""}${x.fits === false ? '<span class="tag o">too big for this Mac</span>' : ""}${g4(x) ? '<span class="tag b">tools · vision</span>' : ""}</div>
      <div class="acts-row">${use(x.name)}</div></div>`;
  const rec = (r) => `<div class="card" data-rec="${esc(r.name)}"><div><h3>${esc(r.label)}</h3><div class="sub">${esc(r.name)} · ${esc(r.size)}</div></div>
      <p>${esc(r.note)}</p><div class="tags">${r.suggested ? '<span class="tag g">suggested for this Mac</span>' : ""}${r.fits ? "" : '<span class="tag o">too big for this Mac</span>'}${r.installed ? '<span class="tag">installed</span>' : ""}</div>
      <div class="acts-row">${r.installed ? use(r.name) : `<button class="btn sm" data-pull="${esc(r.name)}">${icon("download")}Pull</button><span class="bar" hidden><i></i></span><span class="muted pull-text" style="font-size:12px"></span>`}</div></div>`;
  const oa = m.openai || { models: [] };
  const hw = m.hardware;
  page("view-models", "Models", lead, `
    ${hw ? `<div class="sec"><div class="note"><b>${esc(hw.chip)} · ${esc(hw.ram_gb)} GB</b>${hw.gpu_gb ? ` · about ${esc(hw.gpu_gb)} GB for models on the GPU` : ""}<br>${esc(hw.note)}</div></div>` : ""}
    <div class="sec"><h2>On this Mac <span class="count">${m.local.length}</span></h2>${m.ollama ? (m.local.length ? `<div class="cards">${m.local.map(local).join("")}</div>` : `<div class="note">No local models yet. Pull a Gemma 4 build below.</div>`) : `<div class="note">Ollama isn’t running. Start it with <code>ollama serve</code> or open the Ollama app.</div>`}</div>
    <div class="sec"><h2>Gemma 4 for Forge</h2><div class="cards">${m.recommended.map(rec).join("")}</div></div>
    <div class="sec"><h2>OpenAI-compatible server</h2>${oa.models.length ? `<div class="cards">${oa.models.map((x) => `<div class="card ${x.name === m.selected ? "sel" : ""}"><div><h3>${esc(prettyModel(x.name))}</h3><div class="sub">${esc(oa.url)}</div></div><div class="acts-row">${use(x.name)}</div></div>`).join("")}</div>`
      : `<div class="note">Run llama-server, LM Studio or mlx_lm.server, then set <code>FORGE_OPENAI_URL</code> and <code>FORGE_ROUTE=openai</code> in <code>.env</code>.${oa.url ? ` Looking at <code>${esc(oa.url)}</code>.` : ""}</div>`}</div>
    <div class="sec"><h2>Hosted Gemma</h2><div class="cards">${(m.aistudio || []).map((h) => `<div class="card ${h.name === m.selected ? "sel" : ""}"><div><h3>${esc(prettyModel(h.name))}</h3><div class="sub">Google AI Studio</div></div>
      <p>${h.available ? "For machines that can’t run Gemma locally. Needs internet, and your messages go to Google." : "Add <code>GEMINI_API_KEY</code> to <code>.env</code> (free at aistudio.google.com) and restart Forge."}</p><div class="acts-row">${h.available ? use(h.name) : ""}</div></div>`).join("")}</div></div>`);
  $("view-models").onclick = async (e) => {
    const u = e.target.closest("[data-use]");
    if (u) {
      u.disabled = true; u.textContent = "Loading…";
      try { applyModel(await api("/api/models/select", { method: "POST", body: { model: u.dataset.use } })); } catch (er) { toast(er.message, "error"); }
      pageModels();
    }
    const p = e.target.closest("[data-pull]");
    if (p) {
      const c = p.closest(".card"); p.hidden = true; c.querySelector(".bar").hidden = false; c.querySelector(".pull-text").textContent = "Starting…";
      api("/api/models/pull", { method: "POST", body: { model: p.dataset.pull } }).catch((er) => toast(er.message, "error"));
    }
  };
}
window.onPull = (m) => {
  if (m.status === "done") { toast(`${m.name} is ready`); if (S.view === "models") pageModels(); return; }
  const c = document.querySelector(`[data-rec="${CSS.escape(m.name)}"]`);
  if (!c) return;
  const t = c.querySelector(".pull-text"), bar = c.querySelector(".bar");
  if (!t) return;
  bar.hidden = false; c.querySelector("[data-pull]")?.setAttribute("hidden", "");
  if (m.error || m.status === "error") { t.textContent = m.error || "Pull failed"; t.className = "bad-t pull-text"; return; }
  if (m.total) { bar.querySelector("i").style.width = `${(m.completed / m.total) * 100}%`; t.textContent = `${gb(m.completed)} / ${gb(m.total)}`; } else t.textContent = m.status;
};

/* ================= Skills ================= */
async function pageSkills() {
  const lead = `Reusable instructions in the open <a href="https://agentskills.io" target="_blank" rel="noopener">Agent Skills</a> format. Forge reads only names and descriptions up front, and a skill’s full instructions when a task needs them.`;
  const skills = await api("/api/skills").catch(() => []);
  if (S.view !== "skills") return;
  page("view-skills", "Skills", lead, `<div class="sec"><h2>Installed <span class="count">${skills.length}</span></h2>${skills.length ? `<div class="cards">${skills.map((s, i) => `<div class="card">
      <div><h3>${esc(s.name)}</h3><div class="sub">${s.problems.length ? `<span class="bad-t">${esc(s.problems[0])}</span>` : "follows the Agent Skills spec"}</div></div>
      <p>${esc(s.description)}</p><div class="tags">${s.license ? `<span class="tag">${esc(s.license)}</span>` : ""}${s.files.map((f) => `<span class="tag">${esc(f)}</span>`).join("")}</div>
      <div class="acts-row"><button class="btn sm" data-open="${i}" aria-expanded="false">Read SKILL.md</button><button class="btn sm" data-try="${esc(s.name)}">Use in a session</button></div>
      <div class="skill-body prose" hidden></div></div>`).join("")}</div>` : `<div class="note">No skills in the <code>skills/</code> folder.</div>`}</div>`);
  $("view-skills").onclick = (e) => {
    const o = e.target.closest("[data-open]");
    if (o) { const b = o.closest(".card").querySelector(".skill-body"); b.hidden = !b.hidden; o.setAttribute("aria-expanded", String(!b.hidden)); if (!b.hidden) b.innerHTML = MD.render(skills[+o.dataset.open].body); }
    const t = e.target.closest("[data-try]");
    if (t) { S.mode = "computer"; location.hash = "#/"; setTimeout(() => { input.value = `Use the ${t.dataset.try} skill: `; autosize(); input.focus(); }, 80); }
  };
}

/* ================= Evals ================= */
let evalRunning = false;
async function pageEvals() {
  const [r, m] = await Promise.all([api("/api/evals").catch(() => ({ markdown: "" })), api("/api/models").catch(() => ({ local: [], aistudio: [] }))]);
  if (S.view !== "evals") return;
  const opts = [...m.local.map((x) => x.name), ...((m.openai && m.openai.models) || []).map((x) => x.name), ...(m.aistudio || []).filter((x) => x.available).map((x) => x.name)];
  page("view-evals", "Evals", "Does the harness actually help? The same tasks run three ways: <b>native</b> (Ollama’s own tool calling), <b>raw</b> (Forge’s prompt without schema or repair) and <b>forge</b> (everything).", `
    <div class="sec"><div class="form"><select id="ev-model" aria-label="Model">${opts.map((o) => `<option value="${esc(o)}" ${o === S.model.model ? "selected" : ""}>${esc(prettyModel(o))} · ${esc(o)}</option>`).join("")}</select>
      <select id="ev-runs" aria-label="Number of tasks"><option>10</option><option selected>20</option><option>40</option></select>
      <button class="btn primary" id="ev-run" ${evalRunning || !opts.length ? "disabled" : ""}>${icon("play")}${evalRunning ? "Running…" : "Run benchmark"}</button><span class="muted" id="ev-status">${evalRunning ? "This takes a few minutes." : ""}</span></div>
      <div class="log" id="ev-log" hidden></div></div>
    <div class="sec"><h2>Results</h2><div class="prose">${r.markdown ? MD.render(r.markdown) : '<p class="muted">No results yet.</p>'}</div></div>`);
  $("ev-run").onclick = () => {
    evalRunning = true; $("ev-run").disabled = true; $("ev-status").textContent = "Running… this takes a few minutes.";
    api("/api/evals/run", { method: "POST", body: { model: $("ev-model").value, runs: +$("ev-runs").value } }).catch((e) => { evalRunning = false; toast(e.message, "error"); });
  };
}
window.onEval = (m) => {
  if (m.status === "start") evalRunning = true;
  if (m.status !== "done") return;
  evalRunning = false;
  if (S.view !== "evals") { toast("Benchmark finished"); return; }
  pageEvals().then(() => { const l = $("ev-log"); if (l) { l.hidden = false; l.textContent = m.log || ""; } });
};

/* ================= Train ================= */
async function pageTrain() {
  const t = await api("/api/train").catch(() => ({ traces: 0, samples: 0, finished: 0, dataset: 0 }));
  if (S.view !== "train") return;
  const cmd = (c) => `<div class="cmd"><code>${esc(c)}</code><button class="icon-btn sm" data-copytext="${esc(c)}" aria-label="Copy command">${icon("copy")}</button></div>`;
  page("view-train", "Train", `Teach Gemma from Forge’s own successful work. The harness fixes mistakes at run time; fine-tuning with <a href="https://github.com/unslothai/unsloth" target="_blank" rel="noopener">Unsloth</a> teaches the model to make fewer of them.`, `
    <div class="sec"><div class="steps-list">${[["Collect", "Every validated decision is logged on this Mac."], ["Export", "Keep decisions from tasks that finished."], ["Train", "LoRA fine-tune Gemma 4 with Unsloth."], ["Ship", "Export GGUF, then ollama create."], ["Measure", "Run Evals on the new model."]].map(([h, p]) => `<div><h3>${h}</h3><p>${p}</p></div>`).join("")}</div></div>
    <div class="sec"><h2>Your data</h2><div class="stats"><div class="stat"><b>${t.traces}</b><span>sessions recorded</span></div><div class="stat"><b>${t.samples}</b><span>decisions logged</span></div>
      <div class="stat"><b>${t.finished}</b><span>tasks finished</span></div><div class="stat"><b id="ds">${t.dataset}</b><span>examples in the dataset</span></div></div>
      <div class="form" style="margin-top:12px"><button class="btn primary" id="export-btn">${icon("download")}Export dataset</button><span class="muted mono" style="font-size:12px">${esc(t.dataset_path || "")}</span></div></div>
    <div class="sec"><h2>Fine-tune</h2><div class="note">Unsloth trains fastest on an NVIDIA GPU; a free Colab T4 is enough. Copy the dataset there, then run:
      ${cmd("pip install unsloth datasets trl && python training/finetune_unsloth.py --steps 120")}${cmd("cd training/out/forge-gemma && ollama create forge-gemma -f Modelfile")}
      <p class="muted" style="margin:10px 0 0">Below about 200 examples, expect a small gain at best.</p></div></div>`);
  $("export-btn").onclick = async (e) => {
    e.currentTarget.disabled = true;
    try { const r = await api("/api/train/export", { method: "POST" }); $("ds").textContent = r.examples; toast(`Exported ${r.examples} examples`); } catch (er) { toast(er.message, "error"); }
    e.currentTarget.disabled = false;
  };
  $("view-train").onclick = (e) => { const c = e.target.closest("[data-copytext]"); if (c) copy(c.dataset.copytext); };
}

/* ================= Settings ================= */
async function pageSettings() {
  let s;
  try { s = await api("/api/status"); } catch (e) { page("view-settings", "Settings", "", `<div class="note">Couldn’t reach Forge.</div>`); return; }
  if (S.view !== "settings") return;
  const sw = (k, label) => `<button class="switch" role="switch" aria-checked="${!!S.prefs[k]}" aria-label="${esc(label)}" data-pref="${k}"></button>`;
  const seg = (attr, k, opts, cur) => `<div class="seg" role="radiogroup" ${attr}="${k}">${opts.map(([v, l]) => `<button type="button" role="radio" aria-checked="${v === cur}" data-v="${v}">${l}</button>`).join("")}</div>`;
  const row = (t, d, c) => `<div class="sr-row"><div class="l"><b>${t}</b>${d ? `<span>${d}</span>` : ""}</div>${c}</div>`;
  const ok = (good, yes, no) => `<span class="${good ? "ok-t" : "bad-t"}">${good ? yes : no}</span>`;
  const hw = s.hardware || {}, perm = s.permissions || {};
  const voiceCtl = !s.voice ? `<span class="muted">Off (started with --no-voice)</span>` : S.prefs.voice_mode ? `<button class="btn" data-voice="off">Stop voice agent</button>` : `<button class="btn" data-voice="on">${icon("mic")}Continue with voice agent</button>`;
  page("view-settings", "Settings", "How Forge acts, looks and listens. Changes apply right away.", `
    <div class="sec"><h2>Permissions</h2><div class="set">
      ${row("Before acting", "Ask before edits, commands and risky actions, or let Forge act on its own. Dangerous commands are always blocked.", seg("data-seg", "approve", [["ask", "Ask"], ["auto", "Auto-approve"]], S.prefs.approve))}
    </div></div>
    <div class="sec"><h2>Agent</h2><div class="set">
      ${row("Web search", "Let Computer sessions search the web and read pages.", sw("search", "Web search"))}
      ${row("Thinking", "Let Gemma reason before answering. Smarter, but slower.", sw("think", "Thinking"))}
      ${row("Screen agent model", "The model that clicks and types on screen. Same as the session model unless you pick another.", `<select id="agent-model" aria-label="Screen agent model"><option value="">Same as session</option></select>`)}
    </div></div>
    <div class="sec"><h2>Voice agent</h2><div class="set">
      ${row("Voice agent", "Hands-free Forge: “Hey Forge” or a double clap, the on-screen orb and spoken replies. Separate from Studio and never started automatically.", voiceCtl)}
      ${row("Read Studio replies aloud", "Speak answers from Studio sessions too.", sw("speak_replies", "Read replies aloud"))}
      ${row("Orb style", "The voice agent’s orb at the bottom of the screen, or in the notch.", seg("data-seg", "hud_mode", [["full", "Full screen"], ["island", "Island"]], S.prefs.hud_mode))}
    </div></div>
    <div class="sec"><h2>Appearance</h2><div class="set">
      ${row("Theme", "", seg("data-theme-seg", "theme", [["system", "System"], ["light", "Light"], ["dark", "Dark"]], theme()))}
    </div></div>
    <div class="sec"><h2>This Mac</h2><div class="set">
      ${row("Hardware", esc(hw.note || ""), `<span class="muted">${esc(hw.chip || "")}${hw.ram_gb ? ` · ${esc(hw.ram_gb)} GB` : ""}</span>`)}
      ${row("Accessibility", "Lets Forge press real buttons. System Settings → Privacy &amp; Security → Accessibility.", ok(perm.accessibility, "Allowed", "Not allowed"))}
      ${row("Screen Recording", "Lets Forge see the screen. Privacy &amp; Security → Screen Recording.", ok(perm.screen_recording, "Allowed", "Not allowed"))}
      ${row("Voice models", "Kokoro (speaking) and Whisper (listening), both on device.", ok(s.kokoro, "Ready", "Run forge setup"))}
      ${row("AI Studio key", "Optional, for hosted Gemma.", ok(s.has_key, "Set", "Not set"))}
    </div></div>
    <div class="sec"><h2>Shortcuts</h2><div class="set">
      ${row("Send", "<kbd>Enter</kbd> sends, <kbd>Shift</kbd> <kbd>Enter</kbd> adds a line", "")}
      ${row("Interrupt", "<kbd>Esc</kbd> stops the running task (or denies a permission request)", "")}
      ${row("Permission requests", "<kbd>1</kbd> allow once · <kbd>2</kbd> always allow · <kbd>3</kbd> deny", "")}
      ${row("New session", "<kbd>⌘</kbd> <kbd>K</kbd>", "")}
      ${row("Mention a file", "Type <kbd>@</kbd> in a Code session", "")}
      ${row("Focus the message box", "<kbd>/</kbd>", "")}
    </div></div>`);
  api("/api/models").then((m) => {
    const sel = $("agent-model");
    if (!sel) return;
    const names = [...m.local.map((x) => x.name), ...((m.openai && m.openai.models) || []).map((x) => x.name), ...(m.aistudio || []).filter((x) => x.available).map((x) => x.name)];
    sel.innerHTML = `<option value="">Same as session</option>` + names.map((n) => `<option value="${esc(n)}" ${n === S.prefs.agent_model ? "selected" : ""}>${esc(prettyModel(n))} · ${esc(n)}</option>`).join("");
  }).catch(() => {});
}
window.syncSettings = () => {
  const v = $("view-settings");
  v.querySelectorAll("[data-pref]").forEach((x) => x.setAttribute("aria-checked", String(!!S.prefs[x.dataset.pref])));
  v.querySelectorAll("[data-seg]").forEach((g) => g.querySelectorAll("button").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.v === S.prefs[g.dataset.seg]))));
};
$("view-settings").addEventListener("click", (e) => {
  const sw = e.target.closest("[data-pref]");
  if (sw) { const on = sw.getAttribute("aria-checked") !== "true"; sw.setAttribute("aria-checked", String(on)); setPref(sw.dataset.pref, on); }
  const b = e.target.closest("[data-seg] button, [data-theme-seg] button");
  if (b) {
    b.parentElement.querySelectorAll("button").forEach((x) => x.setAttribute("aria-checked", String(x === b)));
    if (b.parentElement.dataset.seg) setPref(b.parentElement.dataset.seg, b.dataset.v); else setTheme(b.dataset.v);
  }
  const vc = e.target.closest("[data-voice]");
  if (vc) voiceAgent(vc.dataset.voice === "on");
});
$("view-settings").addEventListener("change", async (e) => {
  if (e.target.id !== "agent-model") return;
  await setPref("agent_model", e.target.value);
  toast(e.target.value ? `Screen agent: ${prettyModel(e.target.value)}` : "Screen agent uses the session model");
});

window.PAGES = { models: pageModels, skills: pageSkills, evals: pageEvals, train: pageTrain, settings: pageSettings };
