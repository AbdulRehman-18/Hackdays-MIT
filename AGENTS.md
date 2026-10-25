# Forge: Project Guide and Rules for AI Agents

Forge is the owner's Monday assistant (`~/Developer/monday`) rebuilt on open-weight models: a voice-controlled
assistant for macOS that sees the screen and operates the computer, powered by Gemma 4 running on the Mac through
one harness that makes a small model reliable (constrained decoding, repair, planning, a policy gate).

**Any AI coding agent must read this file before changing code and follow section 9.** The owner's standing
decisions: Forge behaves, sounds and looks exactly like Monday (its `AGENTS.md` sections 5-7 are the reference),
the brain is local Gemma, the HUD is Monday's `hud.html` unchanged, and the name is "Forge" ("Hey Forge").

---

## 1. Quick start

```bash
uv sync --extra app                   # + --extra voicelock for the speaker lock
cp .env.example .env
uv run forge setup                    # pulls the Gemma models for this Mac and the Kokoro voice
uv run forge doctor                   # hardware profile, models, permissions
uv run forge app                      # Studio window + HUD + voice
uv run forge voice                    # voice only: "Hey Forge" or a double clap
uv run forge serve                    # Studio in the browser at http://127.0.0.1:8765
uv run forge ask "weather in London and save it as a note"
uv run forge run "open calculator and compute 25 times 4"   # the screen agent
uv run forge enroll                   # voice lock (FORGE_VOICE_LOCK=1)
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
| `src/forge/assistant/static/hud.html` | **Monday's HUD, byte for byte** (only the title and one demo caption say Forge) |
| `src/forge/assistant/static/` (other files) | Forge Studio: chat, Model hub, Skills, Evals, Train, Settings |
| `src/forge/assistant/server.py` | 127.0.0.1 server: Studio, REST API, one event WebSocket |
| `src/forge/tools/` | device tools: apps, web, research, files, Apple apps and timers, system, media, senses, screen, AX |
| `skills/` | Agent Skills (agentskills.io), loaded on demand |
| `training/`, `evals/` | fine-tuning on Forge's own traces; benchmark results |

---

## 4. Listening, personality and voice

Monday's rules, unchanged (see `monday/AGENTS.md` sections 5 and 6 for the reasons):
- A command needs "Hey Forge", a double clap, or the conversation window (`FORGE_CONVERSE_S`, 8 s after a reply,
  and while Forge works or talks). Speech is detected with webrtcvad and a room-noise floor, not loudness; Whisper
  pieces with low confidence are dropped; while busy, a phrase needs two real words or a stop word; the same
  command within 20 s without the wake word is ignored; unfinished sentences wait `FORGE_FINISH_S`.
- The mic stays open while Forge talks, so its own words are filtered out (numbers normalised).
- Space tapped while Forge works or talks interrupts and listens; Esc stops everything. Both are ignored while
  Forge sends its own keys (`runtime.self_typing`). Right Option held is push-to-talk.
- Personality: cute, cheerful, bubbly and warm, smart like JARVIS, never sarcastic. Spoken replies are 1-2 short
  sentences, no markdown, no lists, no emojis. It lives in `harness/chat.py` (`ANSWER`, `STYLE["voice"]`).
- Voice: Kokoro on device, `af_bella*0.6+af_sky*0.4` at 1.12. Speech: Whisper large-v3-turbo (small.en only on
  8 GB Macs or with `FORGE_STT_MODEL=fast`).

---

## 5. Look

- **HUD**: `static/hud.html` is Monday's file. Its design system is `monday/AGENTS.md` section 7 (glass orb and
  wisps, light rising from the orb, captions, Dynamic Island, the doodle cursor). Never edit it except to keep it
  identical to Monday's. The window must stay hidden from screenshots and click-through.
- **Studio**: Monday's design language: dark glass surfaces, blue `#0a6cff`, cyan `#5ad8ff`, white, orange
  `#ff8a00`, red `#ff2d2d`, a touch of green `#2fe07a`; **no purple or violet**, no rainbow or conic borders,
  system font, no external CDNs (works offline).
- Forge's cursor is drawn in the HUD; the user's real cursor is never hidden or recoloured, and every click
  borrows it and puts it back exactly (`tools/screen.py` `borrow`).

---

## 6. Hardware profiles

| Memory (Apple Silicon) | Chat and voice | Screen agent | Context | Whisper |
|---|---|---|---|---|
| 8 GB | gemma4:e2b | gemma4:e2b | 8K | small.en |
| 16 GB | gemma4:e4b | gemma4:e4b | 16K | large-v3-turbo |
| 24-36 GB | gemma4:e4b | gemma4:12b-it-qat | 32K | large-v3-turbo |
| 48 GB+ | gemma4:e4b | gemma4:26b-a4b-it-qat | 32K | large-v3-turbo |
| no Apple GPU | gemma4:e2b | gemma4:e2b | 8K | faster-whisper small.en |

---

## 7. Configuration

All settings are in `.env.example`, with their defaults and meaning. Routes: `FORGE_ROUTE=local` (Ollama),
`openai` (`FORGE_OPENAI_URL`, `FORGE_OPENAI_MODEL`: Ollama `/v1`, llama.cpp, LM Studio, MLX) or `aistudio`.
Files Forge writes: `~/.forge/` (settings, chats, traces, uploads, models, voiceprint, logs) and
`~/Documents/Forge Notes/` (study notes, scrapes).

---

## 8. Measured behaviour (keep these numbers true)

| | Result |
|---|---|
| Compound requests, gemma4:e4b, 8 tasks | 8/8 with planning (5/8 before) |
| LOCAL intents | instant; weather < 1 s |
| Warm harness answer | first words in ~1.6-2.4 s |
| Whisper turbo on an M4 | ~0.8 s a command |
| Noisy room (fan at -36 / -30 dB) | commands with the wake word route; noise and strangers don't (`tests/test_voice.py`) |

---

## 9. Rules for AI agents (mandatory)

### Never change without the owner asking explicitly
1. The HUD (`static/hud.html`) and the Studio design language (section 5). No purple. No new HUD widgets.
2. The voice and personality (section 4): Kokoro blend and speed, the tone of `ANSWER`.
3. The listening behaviour (section 4): wake phrases, clap, chime, conversation window, echo filter, loop guard,
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
- Model behaviour: run the compound-request probe or `uv run forge bench` on the real model and report numbers.
- Do not create real reminders, notes, messages or purchases while testing; stub the tools.
- Report honestly what was and was not tested (for example the live microphone, or the screen agent on screen).

### Never
- Never make the HUD capturable in screenshots or clickable; never hide or move the user's cursor for good.
- Never remove the kill switches (Esc, "stop", the step cap, the cycle guard).
- Never commit or overwrite `.env`, `~/.forge/settings.json` or the user's documents.

---

## 10. Decisions log

| Decision | Reason |
|---|---|
| Forge = Monday on Gemma | owner: "make sure Monday has been copied exactly", on open weights for the hackathon |
| Monday's listening engine, Whisper turbo | a thinner listener with small.en answered room noise as commands ("11.", "I'm sorry") |
| Planning before deciding | small models answered after the first half of "X and Y"; 5/8 → 8/8 |
| Compound requests skip LOCAL and router | "weather in London and save it as a note" was answered with the weather only |
| One decision core with focused repair | two loops had drifted apart; wrong arguments are fixed against that tool's schema only |
| Hardware profiles | e4b plus a second model does not fit a 16 GB Mac: Ollama swapped them and dropped connections |
| HUD ignores Forge's own keys | the agent typing a space would otherwise interrupt its own task |
