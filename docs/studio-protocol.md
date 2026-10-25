# Monday Studio ↔ server protocol

Studio is a single-page app served from `src/forge/assistant/static/` by `server.py` (127.0.0.1 only). Flat files
only (`/static/<name>`), no CDNs, no web fonts: it must work offline.

## Modes and sessions

Every chat ("session") has a `mode` and, in Code mode, a `workspace`:

| mode | what Monday can do |
|---|---|
| `code` | work inside `workspace` (a project folder): `list_files`, `search_code`, `read_file`, `write_file`, `edit_file`, `run_command`. Runs autonomously (up to 40 steps) until it calls done. |
| `computer` | Mac tasks: apps, web search and pages, news, weather, files, Apple Reminders / Notes / Calendar, timers, screen reading, webcam, and `computer_use` (the screen agent clicks and types). |

Chat JSON (`GET /api/chats/{id}`):
```json
{"id": "...", "title": "...", "mode": "code", "workspace": "/Users/me/proj", "created": 0, "updated": 0,
 "messages": [
   {"role": "user", "content": "...", "time": 0, "source": "text", "attachments": [{"id", "name", "kind"}]},
   {"role": "assistant", "content": "markdown", "time": 0, "model": "gemma4:e4b", "thinking": "",
    "plan": [{"task", "tool", "status": "todo|done|skipped"}],
    "steps": [{"id": "s1", "tool": "edit_file", "args": {...}, "thought": "...", "status": "ok|error|denied|blocked|skip",
               "result": "text", "ms": 120, "diff": "unified diff or absent", "path": "src/app.py or absent",
               "exit_code": 0, "repaired": false}],
    "stats": {"route": "instant|router|harness", "seconds": 4.2, "output_tokens": 300, "prompt_tokens": 2000}}
 ]}
```
`GET /api/chats` → `[{"id", "title", "mode", "workspace", "updated"}]` newest first.
`PATCH /api/chats/{id}` body any of `{"title", "mode", "workspace"}`. `DELETE /api/chats/{id}`.

## Workspaces (Code mode)
- `GET /api/workspaces` → `{"recent": [{"path", "name", "branch"}]}` (most recent first).
- `POST /api/workspaces/pick` → opens the macOS folder chooser; `{"path", "name", "branch"}` or `{"cancelled": true}`.
- `POST /api/workspaces/check` body `{"path"}` → `{"ok": true, "path", "name", "branch"}` or `{"ok": false, "error"}`.
- `GET /api/workspaces/files?path=<workspace>&q=<text>` → `{"files": ["src/app.py", ...]}` (up to 50, for @-mentions).

## WebSocket `/ws`

Client → server:
```
{"type": "send", "chat_id": null|"id", "text": "...", "attachments": [upload ids],
 "options": {"mode": "code"|"computer", "workspace": "/path" (code), "approve": "ask"|"auto",
             "search": bool, "think": bool, "deep": bool}}
{"type": "approve", "id": "...", "allow": true|false, "always": true|false}   always = don't ask again in this chat
{"type": "cancel"}                       stop the running Studio task
```
Server → client (every message has `type`):
- `hello` `{state, name, model, prefs, running_chat, voice}` on connect.
- `state` `{state: idle|thinking|acting|speaking|listening|waiting}`.
- `chat_created` `{id, title, mode, workspace}`; `user_message` `{chat_id, message}`.
- `run` `{chat_id, kind, ...}` with kind:
  - `start`
  - `plan` `{items}` / `plan_update` `{items}` — the checklist, live
  - `tool_call` `{id, tool, args, thought, repaired}`
  - `tool_result` `{id, result, status, ms, diff?, path?, exit_code?}`
  - `screen_plan` `{steps}`, `screen_action` `{step, tool, args, thought}`, `screen_result` `{step, tool, result}` (computer_use sub-steps)
  - `progress` `{text}` (a status line), `repair` `{error}`
  - `thinking` `{text}` (delta), `answer_start`, `delta` `{text}`, `answer_end` `{prompt_tokens, output_tokens, seconds}`
  - `error` `{text}`, `stopped`, `done` `{message}` (the saved assistant message)
- `approval` `{id, chat_id, tool, args, reason, preview?}` — preview is a unified diff for edits, or the command for run_command. Answer with `approve`.
- `approval_done` `{id, allow}`.
- `model` `{model, route, format, quant, params, context, max_context?, offline?}`; `prefs` `{prefs}`.
- `pull` `{name, status, total, completed, error}`; `toast` `{level, text}`; `notice` `{text}`; `eval` `{status, ...}`.
- Voice agent only (Studio may show a small indicator): `level`, `caption`, `wake`, `transcript` `{text, chat_id}`.

## Other REST
- `GET /api/status` → `{state, name, model, prefs, permissions, voice, kokoro, has_key, hardware}`.
- Models: `GET /api/models` (→ `{ollama, local[], recommended[], openai{}, aistudio[], selected, hardware}`),
  `POST /api/models/select {model}`, `POST /api/models/pull {model}`.
- `POST /api/prefs` any of `{approve, search, think, speak_replies, hud_mode, agent_model}`.
- `POST /api/upload` (multipart `file`) → `{id, name, kind, size}`; `GET /api/uploads/{id}`.
- Voice agent: `POST /api/voice-agent {"on": true|false}` → `{voice_mode, hud}`. "Continue with voice agent" calls it
  with on=true; Studio keeps working independently, and voice never stops Studio tasks.
- `GET /api/skills`, `GET /api/evals`, `POST /api/evals/run {model, runs}`, `GET /api/train`, `POST /api/train/export`.
