# Monday (open-weight edition): Project Guide and Rules for AI Agents

Monday is two products on one harness, powered by Gemma 4 running on the Mac:
- **Monday Studio**: "a proper Claude Code / Codex for Ollama". A desktop app with two modes: **Code** (a project
  folder: explore, edit with diffs, run commands, keep going until the tests pass) and **Computer** (Mac tasks:
  apps, web, files, reminders, the screen agent). It looks and feels like the Claude Code desktop app.
- **The voice agent**: the owner's Monday assistant (`~/Developer/monday`) on open weights: "Hey Monday", Monday's
  HUD, listening rules, voice and personality. **It is opt-in** ("Continue with voice agent" or `monday voice`) and
  never runs inside Studio on its own or interrupts a Studio task.

**Any AI coding agent must read this file before changing code and follow section 10.**

---

## 1. Quick start

```bash
uv sync --extra app                   # + --extra voicelock for the speaker lock
cp .env.example .env
uv run monday setup                    # pulls the Gemma models for this Mac and the Kokoro voice
uv run monday doctor                   # hardware profile, models, permissions
uv run monday app                      # Studio window + HUD + voice
uv run monday voice                    # voice only: "Hey Monday" or a double clap
uv run monday serve                    # Studio in the browser at http://127.0.0.1:8765
uv run monday ask "weather in London and save it as a note"
uv run monday run "open calculator and compute 25 times 4"   # the screen agent
uv run monday enroll                   # voice lock (FORGE_VOICE_LOCK=1)
```

macOS permissions for the terminal: Accessibility, Screen Recording, Microphone, Camera, Input Monitoring, and
Automation for Reminders, Calendar, Notes and the browser.

---

## 2. How it works

```
 mic ─► voice.Listener (Monday's engine: webrtcvad + room-noise floor, double clap, Whisper large-v3-turbo,
        confidence filter, echo filter, wake word / conversation window, loop guard, unfinished-sentence wait)
   ▼
 session.send  (a new command interrupts the running one)
   ▼
 brain.Router (no model)                       harness (Gemma 4 through the backend for the route)
   1. LOCAL regex intents: time, timers,  ──►   3. plan: compound requests become a checklist (planner.py)
      weather, volume, HUD mode...              4. decide: constrained JSON step, repair once (core.py)
   2. router patterns: YouTube, draw,              → tool → policy gate → run → scratchpad → decide again
      camera, open file/app, read screen           → computer_use → the screen agent (agent.py): observe
   compound requests skip 1-2                         (UI elements + screenshot) → one action → repeat
   ▼                                            5. respond: the answer streams, grounded in tool results
 voice.Mouth (Kokoro, sentence by sentence) ─► HUD (hud_native.py hosts Monday's hud.html) and Studio (WebSocket)
```

---

## 3. File map

| Path | What it does |
|---|---|
| `src/forge/config.py` | every setting, from `.env`, defaults from the hardware profile |
| `src/forge/hardware.py` | chip and memory detection, per-machine models, context and Whisper size |
| `src/forge/harness/core.py` | the one decision step (constrained decode, validate, focused repair) and the one policy gate |
| `src/forge/harness/chat.py` | the chat agent: plan, decide, tools, skills, streamed answer |
| `src/forge/harness/agent.py` | the screen agent: observe, decide one action, step cap, cycle guard, screen-change check |
| `src/forge/harness/planner.py` | request checklist (chat) and step plan (screen agent) |
| `src/forge/harness/schema.py`, `tools.py`, `policy.py`, `skills.py` | the tool-call protocol, typed tools, the gate, Agent Skills |
| `src/forge/harness/backends/` | `ollama` (local), `openai` (any OpenAI-compatible server), `aistudio` (hosted Gemma) |
| `src/forge/assistant/brain.py` | Monday's LOCAL intents and router patterns |
| `src/forge/assistant/session.py` | the one session behind every face: chats, approvals, voice, model hub, keys |
| `src/forge/assistant/voice.py`, `voiceprint.py`, `enroll.py` | Monday's listening engine, Kokoro voice, speaker lock |
| `src/forge/assistant/hud_native.py` | Monday's HUD host: transparent click-through window, 60 fps `H.*` feed, key monitor |
| `src/forge/assistant/static/hud.html` | **Monday's HUD, byte for byte** (only the title and one demo caption say Monday) |
| `src/forge/assistant/static/` (other files) | Monday Studio (`index.html`, `style.css`, `app.js`, `pages.js`, `md.js`): sessions, Code / Computer, activity feed, diffs, permissions, Models, Skills, Evals, Train, Settings |
| `src/forge/tools/code.py` | Code mode: workspace tools, orientation, finish check, edit previews |
| `docs/studio-protocol.md` | the Studio ↔ server contract (REST + WebSocket events) |
| `src/forge/assistant/server.py` | 127.0.0.1 server: Studio, REST API, one event WebSocket |
| `src/forge/tools/` | device tools: apps, web, research, files, Apple apps and timers, system, media, senses, screen, AX |
| `skills/` | Agent Skills (agentskills.io), loaded on demand |
| `training/`, `evals/` | fine-tuning on Monday's own traces; benchmark results |

---

## 4. Code mode (Studio)

`tools/code.py`: `list_files`, `search_code` (ripgrep), `read_file` (numbered), `edit_file` (exact, unique
old_text; indentation of copied text is matched), `write_file`, `run_command` (zsh login shell in the project, no
colours, timeout). Paths cannot leave the project. Before the first step the model gets the file list, git status
and a test command that is checked to work (`orientation`). The loop (`ChatAgent`, up to `FORGE_CODE_MAX_STEPS`=40)
will not finish while the last command failed with no fix since, after editing without re-checking, or after only
describing a requested change (`finish_check`, two push-backs). A repeated call only counts while nothing changed;
the third gets a warning, the fourth stops the run. The policy gate asks before edits and commands; plainly
read-only commands run; destructive ones (`rm -rf ~`, `sudo`, `curl | sh`, force push) are blocked. "Always allow"
is remembered per session for that command's program (`$ npm test`) or for all edits.

Measured on gemma4:e4b: fixed a failing test (7 steps, 41 s); added `median` with 3 tests (all pass); through the
real UI, added `Cart.discount()` with a test and ran the tests (49 s, exit 0).

---

## 5. Listening, personality and voice

Monday's rules, unchanged (see `monday/AGENTS.md` sections 5 and 6 for the reasons):
- A command needs "Hey Monday", a double clap, or the conversation window (`FORGE_CONVERSE_S`, 8 s after a reply,
  and while Monday works or talks). Speech is detected with webrtcvad and a room-noise floor, not loudness; Whisper
  pieces with low confidence are dropped; while busy, a phrase needs two real words or a stop word; the same
  command within 20 s without the wake word is ignored; unfinished sentences wait `FORGE_FINISH_S`.
- The mic stays open while Monday talks, so its own words are filtered out (numbers normalised).
- Space tapped while Monday works or talks interrupts and listens; Esc stops everything. Both are ignored while
  Monday sends its own keys (`runtime.self_typing`). Right Option held is push-to-talk.
- Personality: cute, cheerful, bubbly and warm, smart like JARVIS, never sarcastic. Spoken replies are 1-2 short
  sentences, no markdown, no lists, no emojis. It lives in `harness/chat.py` (`ANSWER`, `STYLE["voice"]`).
- Voice: Kokoro (82M, on device), `af_bella*0.6+af_sky*0.4` at 1.12. Speech: Whisper small.en on the Apple GPU
  (~0.2 s a command; the owner wants light and fast). `FORGE_STT_MODEL=accurate` picks large-v3-turbo.

---

## 6. Look

- **HUD**: `static/hud.html` is Monday's file. Its design system is `monday/AGENTS.md` section 7 (glass orb and
  wisps, light rising from the orb, captions, Dynamic Island, the doodle cursor). Never edit it except to keep it
  identical to Monday's. The window must stay hidden from screenshots and click-through.
- **Studio**: the Codex / ChatGPT desktop-app look (owner's reference screenshots, 2026-10-25). Sidebar: New
  session, Search, Skills, Models; "Projects" with each Code folder and its sessions nested underneath ("In progress"
  or "4h"), a "Computer" group, Settings at the bottom. In the app window the title bar is transparent, the traffic
  lights sit in the sidebar and the sidebar shows macOS's frosted material (`assistant/window_native.py`, page flag
  `?native=1`). Start screen: a centered mark and "Let's build · <project> ⌄" (Code) or "What should we work on?"
  (Computer), one big rounded composer (+, "Ask for approval", model, hold-to-dictate mic, round send), a tray under
  it (project, branch, Voice agent) and three suggestion cards. A Code | Computer pill sits top centre. Inside a
  session: compact activity rows that expand to diffs and output, a live todo plan, inline permission prompts
  (Allow once / Always allow in this session / Deny), a status line. Neutral light and dark themes, one accent
  (blue `#0a6cff`) used sparingly, green / red / orange only for diffs and status; system font, monospace only for
  paths, commands and code; no purple, no CDNs (works offline). Protocol: `docs/studio-protocol.md`; `#/demo`
  replays a scripted session.
- Monday's cursor is drawn in the HUD; the user's real cursor is never hidden or recoloured, and every click
  borrows it and puts it back exactly (`tools/screen.py` `borrow`).

---

## 7. Hardware profiles

| Memory (Apple Silicon) | Chat and voice | Screen agent | Context | Whisper |
|---|---|---|---|---|
| 8 GB | gemma4:e2b | gemma4:e2b | 8K | small.en |
| 16 GB | gemma4:e4b | gemma4:e4b | 16K | small.en |
| 24-36 GB | gemma4:e4b | gemma4:e4b | 32K | small.en |
| 48 GB+ | gemma4:e4b | gemma4:e4b | 32K | small.en |

Light by default (owner: no heavy models); 12B QAT and 26B A4B are optional picks in the Model hub.
| no Apple GPU | gemma4:e2b | gemma4:e2b | 8K | faster-whisper small.en |

---

## 8. Configuration

All settings are in `.env.example`, with their defaults and meaning. Routes: `FORGE_ROUTE=local` (Ollama),
`openai` (`FORGE_OPENAI_URL`, `FORGE_OPENAI_MODEL`: Ollama `/v1`, llama.cpp, LM Studio, MLX) or `aistudio`.
Files Monday writes: `~/.forge/` (settings, chats, traces, uploads, models, voiceprint, logs) and
`~/Documents/Monday Notes/` (study notes, scrapes).

---

## 9. Measured behaviour (keep these numbers true)

| | Result |
|---|---|
| Compound requests, gemma4:e4b, 8 tasks | 8/8 with planning (5/8 before) |
| LOCAL intents | instant; weather < 1 s |
| Warm harness answer | first words in ~1.6-2.4 s |
| Whisper small.en on an M4 | ~0.2 s a command (turbo ~0.8 s) |
| Noisy room (fan at -36 / -30 dB) | commands with the wake word route; noise and strangers don't (`tests/test_voice.py`) |

---

## 10. Rules for AI agents (mandatory)

### Never change without the owner asking explicitly
1. The HUD (`static/hud.html`) and the Studio design language (section 6). No purple. No new HUD widgets.
   Studio and the voice agent stay separate: separate task slots, voice never starts by itself.
2. The voice and personality (section 5): Kokoro blend and speed, the tone of `ANSWER`.
3. The listening behaviour (section 5): wake phrases, clap, chime, conversation window, echo filter, loop guard,
   barge-in, Esc and Space.
4. The brain: Gemma through the harness. Do not add a cloud provider as the default.
5. The routing order: `brain.Router` LOCAL → router patterns → harness (plan → decide → tools / screen agent).
   New instant phrases go in `Router.local`, deterministic patterns in `Router.fast`, everything else is a tool.
6. Defaults in `config.py`, `hardware.py` and `.env.example`.

### Architecture
- One harness: every model decision goes through `harness/core.decide`, every action through `core.gate`.
  Do not add a second loop with its own parsing or repair.
- Tools are plain typed functions (`@tool`); their docstrings are what the model reads, keep them precise.
  Never name an argument after a JSON-schema keyword without a test (an argument called `title` was once
  stripped from every schema).
- Everything the faces show goes through `session.emit`; the HUD only through `hud_native.on_event`.
  All speech goes through `voice.Mouth.say`.
- Long work checks the cancel event; risky actions (send, delete, pay, post) must reach the policy gate.
- Nothing leaves the Mac on the local route. Never log secrets or put them in URLs.

### Testing (before saying "done")
- `uv run ruff check src tests` and `uv run pytest -q` (no model needed);
  `FORGE_SLOW_TESTS=1 uv run pytest -q tests/test_voice.py` runs real Whisper on a synthetic noisy room.
- Model behaviour: run the compound-request probe or `uv run monday bench` on the real model and report numbers.
- Do not create real reminders, notes, messages or purchases while testing; stub the tools.
- Report honestly what was and was not tested (for example the live microphone, or the screen agent on screen).

### Never
- Never make the HUD capturable in screenshots or clickable; never hide or move the user's cursor for good.
- Never remove the kill switches (Esc, "stop", the step cap, the cycle guard).
- Never commit or overwrite `.env`, `~/.forge/settings.json` or the user's documents.

---

## 11. Decisions log

| Decision | Reason |
|---|---|
| Monday on Gemma, named Monday | owner: "make sure Monday has been copied exactly"; later "name it MONDAY itself, no need for Forge" |
| Studio = Claude Code desktop, Code + Computer | owner rejected a glassy chat clone: "I want a proper Claude Code desktop like feel" |
| Studio restyled after the Codex / ChatGPT apps | owner shared Codex and ChatGPT "Work" screenshots: projects with nested sessions, centered composer, tray, cards |
| Studio window: text selection on | pywebview disables it by default; answers could not be copied |
| Voice agent opt-in, own task slot | voice mode always on inside Studio cancelled typed tasks ("it is stopping every time") |
| Whisper small.en default | owner: light and fast, no heavy models; Monday's filters (VAD, confidence) handle the noise |
| Code-mode orientation + finish_check | without them Gemma ran `test`, invented files, or described a fix instead of making it |
| Monday's listening engine, Whisper turbo | a thinner listener with small.en answered room noise as commands ("11.", "I'm sorry") |
| Planning before deciding | small models answered after the first half of "X and Y"; 5/8 → 8/8 |
| Compound requests skip LOCAL and router | "weather in London and save it as a note" was answered with the weather only |
| One decision core with focused repair | two loops had drifted apart; wrong arguments are fixed against that tool's schema only |
| Hardware profiles | e4b plus a second model does not fit a 16 GB Mac: Ollama swapped them and dropped connections |
| HUD ignores Monday's own keys | the agent typing a space would otherwise interrupt its own task |
