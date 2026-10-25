"""The one assistant session behind every face: Forge Studio (chat), the HUD overlay and voice.

It owns the chats, runs the chat agent on a worker thread, streams every event to all connected faces,
handles approvals ("ask every time" or "approve for me"), the model hub, and the always-on voice mode."""
import base64
import json
import os
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Annotated

import httpx
from pydantic import Field

from .. import runtime
from ..config import HARDWARE, PROFILE, SKILLS_DIR, Settings
from ..hardware import fits
from ..harness import Agent
from ..harness.backends import BackendError, make_backend
from ..harness.chat import ChatAgent, Turn
from ..harness.policy import Policy, Verdict
from ..harness.schema import Action
from ..harness.skills import load as load_skills
from ..harness.tools import tool
from . import chats
from .brain import Router
from .voice import Ears, Listener, Mouth, SentenceSplitter, chime

PREFS = Path.home() / ".forge" / "settings.json"
WORKSPACES = Path.home() / ".forge" / "workspaces.json"
CODE_MAX_STEPS = int(os.getenv("FORGE_CODE_MAX_STEPS", "40"))
TWO_WORD = {"npm", "npx", "yarn", "pnpm", "uv", "git", "python", "python3", "cargo", "go", "make", "bun", "pip"}
UPLOADS = Path.home() / ".forge" / "uploads"
STOP_WORDS = ("stop", "cancel", "never mind", "nevermind", "shut up", "be quiet", "quiet", "enough", "forget it")
YES = ("yes", "yeah", "yep", "yup", "sure", "ok", "okay", "do it", "go ahead", "confirm", "allow")
NO = ("no", "nope", "don't", "do not", "cancel", "stop", "deny")
RECOMMENDED = [
    {"name": "gemma4:e2b", "label": "Gemma 4 E2B", "note": "Smallest and fastest. Good on 8 GB Macs.", "gb": 7.2},
    {"name": "gemma4:e4b", "label": "Gemma 4 E4B", "note": "Best balance on a 16 GB Mac.", "gb": 9.5},
    {"name": "gemma4:12b-it-qat", "label": "Gemma 4 12B QAT", "note": "Smarter agent, 4-bit QAT.", "gb": 7.2},
    {"name": "gemma4:26b-a4b-it-qat", "label": "Gemma 4 26B A4B", "note": "MoE, smartest. Needs 24 GB+.", "gb": 16},
]
AISTUDIO_MODELS = ["gemma-4-26b-a4b-it", "gemma-4-31b-it"]


@dataclass
class Prefs:
    model: str = ""            # "gemma4:e4b" (local) or "aistudio:gemma-4-26b-a4b-it"
    agent_model: str = ""      # model for the screen agent; empty = same as model
    approve: str = "ask"       # ask: confirm risky actions; auto: approve them for me
    search: bool = True
    computer: bool = True
    think: bool = False
    speak_replies: bool = False  # read Studio replies aloud too
    voice_mode: bool = False     # always listening for "Hey Forge" / a double clap, HUD on screen
    hud_mode: str = "full"       # full (orb + screen edges) or island (in the notch)


def _match(text: str, words) -> bool:
    t = text.lower().strip(" .!?,")
    return any(t == w or t.startswith(w + " ") for w in words)


class Session:
    def __init__(self, settings: Settings, voice: bool = True):
        self.s = settings
        self.listeners: list[Callable[[dict], None]] = []
        self.state = "idle"
        # Studio and the voice agent are separate: each has its own task slot, so a voice command can only ever
        # stop a voice task, and a typed message only a typed one
        self.runs: dict[str, tuple[threading.Thread, threading.Event]] = {}
        self.running_chat: str | None = None
        self.ctx: dict[threading.Thread, dict] = {}  # per running task: its chat id and workspace
        self.always: dict[str, set[str]] = {}  # chat id -> actions the user allowed for the rest of that chat
        self.approvals: dict[str, dict] = {}
        self.uploads: dict[str, dict] = {}
        self.voice_enabled = voice
        self.voice_chat: str | None = None
        self.prefs = self._load_prefs()
        self.skills = load_skills(SKILLS_DIR) if SKILLS_DIR.exists() else []
        self.policy = Policy()
        self.mouth = Mouth(lambda v: self.emit("level", who="assistant", v=round(v, 3)), self._on_speaking,
                           lambda text: self.emit("caption", who="assistant", text=text))
        self.ears = Ears(lambda v: self.emit("level", who="you", v=round(v, 3)))
        self.listener = Listener(settings.name, self._voice_command, self._voice_wake,
                                 lambda v: self.emit("level", who="you", v=round(v, 3)), self.busy, self.mouth)
        self.hud_on = False  # set by hud_native when the HUD window is up
        self.fast = self.agent_backend = None
        self.select_model(self.prefs.model or self.default_model(), save=False)
        runtime.emit = self._runtime_event
        self.prefs.voice_mode = False  # the voice agent only runs when chosen (forge voice, or "Continue with voice")

    # ---------------- events ----------------
    def emit(self, type_: str, /, **data):
        msg = {"type": type_, **data}
        for fn in list(self.listeners):
            try:
                fn(msg)
            except Exception:
                pass

    def set_state(self, state: str):
        if state != self.state:
            self.state = state
            self.emit("state", state=state)

    def running(self, slot: str | None = None) -> bool:
        return any(t.is_alive() for k, (t, _) in self.runs.items() if slot in (None, k))

    def busy(self) -> bool:
        """For the voice agent: is Forge working on a spoken request, or talking?"""
        return self.running("voice") or self.mouth.speaking

    def _current_cancel(self) -> threading.Event:
        me = threading.current_thread()
        return next((c for t, c in self.runs.values() if t is me), threading.Event())

    def _on_speaking(self, on: bool):
        if on:
            self.set_state("speaking")
        elif not self.running():
            self.set_state("idle")
            if self.prefs.voice_mode:
                self.listener.converse()

    def _runtime_event(self, kind: str, data: dict):
        if kind == "timer":  # Monday's ring: wake the HUD, two Glass chimes, then "Ding! Your ... timer is done!"
            self.emit("wake")
            self.emit("notice", text=data["say"])
            self.emit("caption", who="assistant", text=data["say"])

            def ring():
                for _ in range(2):
                    subprocess.run(["afplay", "/System/Library/Sounds/Glass.aiff"], check=False)
                if self.voice_enabled:
                    self.mouth.say(data["say"])
            threading.Thread(target=ring, daemon=True).start()
        elif kind in ("cursor", "click", "agent_cursor"):
            self.emit(kind, **data)
        elif kind == "progress":
            self.emit("run", chat_id=self.running_chat, kind="progress", text=data["text"])
        elif kind == "image":  # the webcam frame "look" used: shown on the HUD for 7 s
            self.emit("image", jpeg_b64=base64.b64encode(data["jpeg"]).decode())

    # ---------------- preferences & models ----------------
    def _load_prefs(self) -> Prefs:
        try:
            return Prefs(**{k: v for k, v in json.loads(PREFS.read_text()).items() if k in Prefs.__annotations__})
        except (FileNotFoundError, json.JSONDecodeError, TypeError):
            return Prefs()

    def save_prefs(self):
        PREFS.parent.mkdir(parents=True, exist_ok=True)
        PREFS.write_text(json.dumps(asdict(self.prefs), indent=2))

    def set_prefs(self, **changes):
        for k, v in changes.items():
            if k in Prefs.__annotations__ and k not in ("model", "voice_mode"):
                setattr(self.prefs, k, v)
        if "model" in changes:
            self.select_model(changes["model"])
        if "voice_mode" in changes:
            self.set_voice_mode(bool(changes["voice_mode"]))
        self.save_prefs()
        self.emit("prefs", prefs=asdict(self.prefs))

    def default_model(self) -> str:
        """The model for a fresh install: from the route in .env, and on the local route from the hardware profile."""
        if self.s.route == "openai":
            return f"openai:{self.s.openai_model}"
        if self.s.route == "aistudio":
            return f"aistudio:{self.s.aistudio_model}"
        return self.s.fast_model

    def _backend(self, model: str):
        """Model names carry their route: "gemma4:e4b" (Ollama), "openai:<id>" (an OpenAI-compatible server
        at FORGE_OPENAI_URL) or "aistudio:<id>"."""
        for route in ("aistudio", "openai"):
            if model.startswith(route + ":"):
                return make_backend(route, model.split(":", 1)[1], self.s)
        return make_backend("local", model, self.s)

    def select_model(self, model: str, save: bool = True):
        try:
            fast = self._backend(model)
            agent = self._backend(self.prefs.agent_model) if self.prefs.agent_model else fast
        except BackendError as e:
            self.emit("toast", level="error", text=str(e))
            if self.fast is not None:
                return
            model = self.default_model()
            fast = agent = self._backend(model)
        self.fast, self.agent_backend = fast, agent
        runtime.fast = fast
        self.prefs.model = model
        if save:
            self.save_prefs()
        self.emit("model", **self.model_info())

    def model_info(self) -> dict:
        m = self.prefs.model
        route = next((r for r in ("aistudio", "openai") if m.startswith(r + ":")), "local")
        info = {"model": m, "route": route, "context": self.s.num_ctx,
                "format": "", "quant": "", "params": "", "capabilities": []}
        if info["route"] == "local":
            try:
                d = httpx.post(f"{self.s.ollama_url}/api/show", json={"model": m}, timeout=4).json()
                det = d.get("details", {})
                info.update(format=det.get("runner") or det.get("format", ""), quant=det.get("quantization_level", ""),
                            params=det.get("parameter_size", ""), capabilities=d.get("capabilities", []))
                ctx = next((v for k, v in d.get("model_info", {}).items() if k.endswith("context_length")), 0)
                info["max_context"] = ctx
            except (httpx.HTTPError, ValueError):
                info["offline"] = True
        elif route == "openai":
            info.update(format="OpenAI-compatible", server=self.s.openai_url)
        else:
            info.update(format="AI Studio", capabilities=["completion", "vision", "tools"], context=128000)
        return info

    def models(self) -> dict:
        local = []
        try:
            tags = httpx.get(f"{self.s.ollama_url}/api/tags", timeout=4).json().get("models", [])
            loaded = {m["name"] for m in httpx.get(f"{self.s.ollama_url}/api/ps", timeout=4).json().get("models", [])}
            for m in tags:
                det = m.get("details", {})
                if m["name"].endswith(":cloud") or "cloud" in m["name"]:
                    continue
                local.append({"name": m["name"], "size": m.get("size", 0), "family": det.get("family", ""),
                              "params": det.get("parameter_size", ""), "quant": det.get("quantization_level", ""),
                              "format": det.get("format", ""), "loaded": m["name"] in loaded,
                              "modified": m.get("modified_at", "")})
            online = True
        except (httpx.HTTPError, ValueError):
            online = False
        names = {m["name"] for m in local}
        for m in local:
            m["fits"] = fits(m["size"] / 1e9, HARDWARE)
        rec = [{**r, "size": f"≈{r['gb']:g} GB", "installed": r["name"] in names, "fits": fits(r["gb"], HARDWARE),
                "suggested": r["name"] in (PROFILE.fast_model, PROFILE.agent_model)} for r in RECOMMENDED]
        hosted = [{"name": f"aistudio:{m}", "label": m, "available": bool(self.s.gemini_key)} for m in AISTUDIO_MODELS]
        return {"ollama": online, "local": sorted(local, key=lambda m: (m["family"] != "gemma4", m["name"])),
                "recommended": rec, "aistudio": hosted, "openai": self.openai_models(), "selected": self.prefs.model,
                "hardware": {"chip": HARDWARE.chip, "ram_gb": HARDWARE.ram_gb, "gpu_gb": HARDWARE.gpu_budget_gb,
                             "note": PROFILE.note}}

    def openai_models(self) -> dict:
        """Models on the OpenAI-compatible server (llama.cpp, LM Studio, MLX...). Ollama's own /v1 is skipped:
        its models are already listed above."""
        url = self.s.openai_url.rstrip("/")
        if url.startswith(self.s.ollama_url.rstrip("/")) and self.s.route != "openai":
            return {"url": url, "online": False, "models": []}
        try:
            from ..harness.backends.openai import OpenAIBackend
            ids = OpenAIBackend("", url, self.s.openai_key).list_models()
            return {"url": url, "online": True, "models": [{"name": f"openai:{i}", "label": i} for i in ids]}
        except (httpx.HTTPError, ValueError, KeyError):
            return {"url": url, "online": False, "models": []}

    def pull(self, name: str):
        def work():
            try:
                with httpx.stream("POST", f"{self.s.ollama_url}/api/pull", json={"model": name}, timeout=None) as r:
                    for line in r.iter_lines():
                        if not line.strip():
                            continue
                        d = json.loads(line)
                        self.emit("pull", name=name, status=d.get("status", ""), total=d.get("total", 0),
                                  completed=d.get("completed", 0), error=d.get("error", ""))
                self.emit("pull", name=name, status="done")
            except httpx.HTTPError as e:
                self.emit("pull", name=name, status="error", error=str(e))
        threading.Thread(target=work, daemon=True).start()

    # ---------------- uploads ----------------
    def add_upload(self, name: str, data: bytes, mime: str) -> dict:
        from ..tools.files import read_text
        uid = uuid.uuid4().hex[:10]
        UPLOADS.mkdir(parents=True, exist_ok=True)
        safe = "".join(c for c in name if c.isalnum() or c in "._- ")[:80] or "file"
        path = UPLOADS / f"{uid}-{safe}"
        path.write_bytes(data)
        kind = "image" if mime.startswith("image/") else "file"
        item = {"id": uid, "name": name, "kind": kind, "path": str(path), "size": len(data)}
        if kind == "file":
            try:
                item["text"] = read_text(path, 20000)
            except Exception as e:
                item["text"] = f"(could not read {name}: {e})"
        self.uploads[uid] = item
        return {k: v for k, v in item.items() if k != "text"}

    # ---------------- workspaces (Code mode) ----------------
    def workspaces(self) -> list[dict]:
        from ..tools.code import branch
        try:
            paths = json.loads(WORKSPACES.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            paths = []
        return [{"path": p, "name": Path(p).name, "branch": branch(Path(p))} for p in paths if Path(p).is_dir()]

    def remember_workspace(self, path: str):
        try:
            paths = json.loads(WORKSPACES.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            paths = []
        paths = [path] + [p for p in paths if p != path]
        WORKSPACES.parent.mkdir(parents=True, exist_ok=True)
        WORKSPACES.write_text(json.dumps(paths[:12], indent=2))

    # ---------------- approvals ----------------
    @staticmethod
    def approval_key(action: Action) -> str:
        """What "Always allow" remembers: the command's program (two words for npm test, git commit...), all
        edits, or the tool."""
        if action.tool == "run_command":
            words = str(action.args.get("command", "")).split()
            return "$ " + " ".join(words[:2] if words and words[0] in TWO_WORD else words[:1])
        if action.tool in ("edit_file", "write_file"):
            return "edit"
        return action.tool

    def ask_approval(self, action: Action, verdict: Verdict) -> bool:
        ctx = self.ctx.get(threading.current_thread(), {})
        cid = ctx.get("chat") or self.running_chat
        key = self.approval_key(action)
        if self.prefs.approve == "auto" or key in self.always.get(cid or "", set()):
            self.emit("run", chat_id=cid, kind="auto_approved", tool=action.tool, reason=verdict.reason)
            return True
        aid = uuid.uuid4().hex[:8]
        ev, cancel = threading.Event(), self._current_cancel()
        slot = next((k for k, (t, _) in self.runs.items() if t is threading.current_thread()), "text")
        self.approvals[aid] = {"event": ev, "allow": False, "slot": slot, "chat": cid, "key": key}
        prev = self.state
        self.set_state("waiting")
        preview = ""
        if ctx.get("workspace") and action.tool in ("edit_file", "write_file", "run_command"):
            from ..tools.code import preview as code_preview
            preview = code_preview(Path(ctx["workspace"]), action.tool, action.args)
        self.emit("approval", id=aid, chat_id=cid, tool=action.tool, args=action.args, reason=verdict.reason,
                  preview=preview)
        if self.voice_enabled and (slot == "voice" or self.prefs.speak_replies):
            self.mouth.say(f"Should I go ahead? It means {verdict.reason}.")
            self.listener.expect(30)  # a bare "yes" or "no" counts as the answer
        ev.wait(timeout=180)
        allow = self.approvals.pop(aid, {"allow": False})["allow"] and not cancel.is_set()
        self.emit("approval_done", id=aid, allow=allow)
        self.set_state(prev if prev != "waiting" else "acting")
        return allow

    def answer_approval(self, aid: str, allow: bool, always: bool = False):
        a = self.approvals.get(aid)
        if a:
            a["allow"] = allow
            if allow and always and a.get("chat"):
                self.always.setdefault(a["chat"], set()).add(a["key"])
            a["event"].set()

    # ---------------- running a message ----------------
    def interrupt(self, slot: str | None = None):
        """Stop the Studio task (slot "text"), the voice task ("voice"), or both (None)."""
        for k, (_t, cancel) in list(self.runs.items()):
            if slot in (None, k):
                cancel.set()
        if slot in (None, "voice"):
            self.mouth.interrupt()
        for a in list(self.approvals.values()):
            if slot in (None, a.get("slot")):
                a["allow"] = False
                a["event"].set()
        for k, (t, _) in list(self.runs.items()):
            if slot in (None, k) and t.is_alive() and t is not threading.current_thread():
                t.join(timeout=4)
        if not self.running():
            self.set_state("idle")

    # ---------------- keys (from the HUD's global key monitor) ----------------
    def escape(self):
        """Esc: stop the voice agent's task and end its conversation window; it is also the kill switch for the
        screen agent, whichever face started it."""
        if self.state == "acting":
            self.interrupt()
        elif self.busy() or any(a.get("slot") == "voice" for a in self.approvals.values()):
            self.interrupt("voice")
        end = getattr(self.listener, "end_conversation", None)
        if end:
            end()

    def space_tap(self):
        """One Space tap while the voice agent works or talks: stop, chime, listen. Ignored otherwise, so typing
        (and Studio) is unaffected."""
        if not self.prefs.voice_mode or not self.busy():
            return
        self.interrupt("voice")
        listen = getattr(self.listener, "interrupt_listen", None)
        if listen:
            listen()
        else:
            self._voice_wake()
            self.listener.converse()

    def toggle_hud(self):
        self.set_prefs(hud_mode="island" if self.prefs.hud_mode == "full" else "full")

    def set_hud(self, mode: str) -> bool:
        self.set_prefs(hud_mode=mode)
        return self.hud_on

    def send(self, text: str, chat_id: str | None = None, attachments: list[str] | None = None,
             source: str = "text", options: dict | None = None) -> str | None:
        text = text.strip()
        if not text and not attachments:
            return None
        slot = "voice" if source == "voice" else "text"
        pending = next((k for k, a in self.approvals.items() if a.get("slot") == slot), None)
        if pending and (_match(text, YES) or _match(text, NO)):
            self.answer_approval(pending, _match(text, YES))
            return chat_id
        self.interrupt(slot)
        if source == "voice":
            self.emit("caption", who="you", text=text)
            if _match(text, STOP_WORDS) and len(text.split()) <= 3:
                self.mouth.say("Okay.")
                return chat_id
        opts = options or {}
        mode = opts.get("mode") if opts.get("mode") in ("code", "computer") else None
        workspace = str(Path(opts["workspace"]).expanduser()) if opts.get("workspace") else None
        chat = chats.load(chat_id) if chat_id else None
        if chat is None:
            chat = chats.new(chats.title_from(text or "Files"), mode or "computer", workspace or "")
            chats.save(chat)
            self.emit("chat_created", id=chat["id"], title=chat["title"], mode=chat["mode"],
                      workspace=chat["workspace"])
        elif (mode and mode != chat.get("mode")) or (workspace and workspace != chat.get("workspace")):
            chat["mode"] = mode or chat.get("mode", "computer")
            chat["workspace"] = workspace or chat.get("workspace", "")
            chats.save(chat)
        if chat.get("mode") == "code" and chat.get("workspace"):
            self.remember_workspace(chat["workspace"])
        if options:
            self.set_prefs(**{k: v for k, v in options.items() if k in ("search", "computer", "think", "approve")})
        files = [self.uploads[a] for a in attachments or [] if a in self.uploads]
        user = {"role": "user", "content": text, "time": time.time(), "source": source,
                "attachments": [{"id": f["id"], "name": f["name"], "kind": f["kind"]} for f in files]}
        chat["messages"].append(user)
        chats.save(chat)
        self.emit("user_message", chat_id=chat["id"], message=user)
        cancel = threading.Event()
        self.running_chat = chat["id"]
        deep = bool((options or {}).get("deep"))
        task = threading.Thread(target=self._run, args=(chat, user, files, source, cancel, deep), daemon=True)
        self.runs[slot] = (task, cancel)
        task.start()
        return chat["id"]

    def _code_tools(self, workspace: str) -> list:
        from ..tools import research
        from ..tools.code import workspace_tools
        return workspace_tools(Path(workspace)) + (list(research.TOOLS) if self.prefs.search else [])

    def _tools(self, cancel: threading.Event) -> list:
        from ..tools import CHAT, SEARCH
        tools = [t for t in CHAT if self.prefs.search or t.name not in SEARCH]
        if self.prefs.computer:
            tools.append(self._computer_tool(cancel))
        return tools

    def _computer_tool(self, cancel: threading.Event):
        session = self
        names = ", ".join(s.name for s in self.skills) or "none"

        @tool(group="computer")
        def computer_use(goal: Annotated[str, Field(description="the task, as one clear sentence")],
                         skill: Annotated[str, Field(description=f"skill to follow: {names}")] = "none") -> str:
            """Operate apps on the Mac step by step (click, type, navigate) to reach a goal. Use for tasks inside
            apps or websites that no other tool does in one call."""
            from ..tools import SCREEN
            from ..tools.observe import observe
            s = next((k for k in session.skills if k.name == skill), None)
            guide = f"Follow this skill ({s.name}):\n{s.body()}" if s else ""

            def ev(kind, d):
                if kind in ("plan", "action", "result", "policy", "repair", "cycle", "invalid"):
                    session.emit("run", chat_id=session.running_chat, kind=f"screen_{kind}", **d)
                if kind == "action":
                    session.set_state("acting")
            agent = Agent(session.agent_backend, SCREEN, max_steps=session.s.max_steps, observe=observe, on_event=ev,
                          approve=session.ask_approval, cancel=cancel, policy=session.policy,
                          trace_dir=session.s.trace_dir, instructions=guide, plan=session.s.plan)
            try:
                r = agent.run(goal)
            finally:
                from ..tools.screen import show_cursor
                show_cursor(False)  # Forge's cursor flies back into the orb
            return f"{'Done' if r.finished else 'Stopped'} after {len(r.steps)} steps: {r.summary}"
        return computer_use

    def _history(self, chat: dict, upto: int) -> list[Turn]:
        out = []
        for m in chat["messages"][:upto]:
            if m["role"] in ("user", "assistant") and m.get("content"):
                out.append(Turn(m["role"], m["content"]))
        return out

    def _run(self, chat: dict, user: dict, files: list[dict], source: str, cancel: threading.Event,
             deep: bool = False):
        cid = chat["id"]
        t0 = time.perf_counter()
        voice_out = self.voice_enabled and (source == "voice" or self.prefs.speak_replies)
        splitter = SentenceSplitter()
        text, thinking, steps = [], [], []
        stats: dict = {}
        plan: list = []

        def ev(kind: str, d: dict):
            if cancel.is_set():
                return
            if kind == "delta":
                text.append(d["text"])
                if voice_out:
                    for sentence in splitter.feed(d["text"]):
                        self.mouth.say(sentence)
            elif kind == "thinking":
                thinking.append(d["text"])
            elif kind == "tool_call":
                self.set_state("acting")
                steps.append(dict(d))
            elif kind == "tool_result":
                for s in steps:
                    if s["id"] == d["id"]:
                        s.update(result=d["result"], status=d["status"], ms=d.get("ms"))
                self.set_state("thinking")
            elif kind == "answer_start":
                self.set_state("thinking")
            elif kind == "answer_end":
                stats.update(d)
            elif kind in ("plan", "plan_update"):
                plan[:] = d.get("items") or []
            self.emit("run", chat_id=cid, kind=kind, **d)

        self.set_state("thinking")
        self.emit("run", chat_id=cid, kind="start")
        reply, err = None, None
        acks: list[str] = []

        def ack(sentence: str):  # a short "Playing believer." before slow work starts, as Monday does
            acks.append(sentence)
            self.emit("caption", who="assistant", text=sentence)
            self.emit("run", chat_id=cid, kind="progress", text=sentence)
            if voice_out:
                self.mouth.say(sentence)
        mode = chat.get("mode", "computer") if source != "voice" else "computer"
        workspace = chat.get("workspace", "") if mode == "code" else ""
        self.ctx[threading.current_thread()] = {"chat": cid, "workspace": workspace}
        try:
            if mode == "code" and not (workspace and Path(workspace).is_dir()):
                raise ValueError("Code mode needs a project folder: open one for this session first.")
            routed = Router(ack, self.set_hud).route(user["content"]) if not files and mode == "computer" else None
            if routed is not None:
                stats = {"route": routed[0]}
                reply = routed[1]
                if reply:
                    self.emit("run", chat_id=cid, kind="delta", text=reply)
                else:
                    reply = " ".join(acks)  # it already spoke: keep the acknowledgement as the chat reply
                    voice_out = False
            else:
                content = user["content"]
                for f in files:
                    if f["kind"] == "file":
                        content += f"\n\n[Attached file {f['name']}]\n{f.get('text', '')}"
                images = [Path(f["path"]).read_bytes() for f in files if f["kind"] == "image"]
                if mode == "code":
                    from ..tools.code import GUIDE, branch, finish_check, orientation
                    b = branch(Path(workspace))
                    guide = GUIDE.format(root=workspace, branch=f" (git branch {b})" if b else "",
                                         orientation=orientation(Path(workspace)))
                    agent = ChatAgent(self.agent_backend, self._code_tools(workspace), self.s.name, [], self.policy,
                                      self.ask_approval, ev, cancel, max_steps=CODE_MAX_STEPS, mode="code",
                                      think=self.prefs.think, plan=False, instructions=guide,
                                      finish_check=finish_check)
                else:
                    agent = ChatAgent(self.fast, self._tools(cancel), self.s.name, self.skills, self.policy,
                                      self.ask_approval, ev, cancel, mode="voice" if source == "voice" else "chat",
                                      think=self.prefs.think, deep=deep, plan=self.s.plan)
                upto = len(chat["messages"]) - 1
                result = agent.run(self._history(chat, upto), Turn("user", content or "(see attachments)", images))
                reply = result.text
        except (BackendError, ValueError) as e:
            err = str(e)
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
        self.ctx.pop(threading.current_thread(), None)
        if cancel.is_set():
            reply = "".join(text).strip() or None
            self.emit("run", chat_id=cid, kind="stopped")
        if err:
            reply = f"Sorry, I couldn't finish that. {err}"
            self.emit("run", chat_id=cid, kind="error", text=err)
        if voice_out and not cancel.is_set():
            for sentence in splitter.flush() if text else ([reply] if reply else []):
                self.mouth.say(sentence)
        msg = {"role": "assistant", "content": reply or "", "time": time.time(), "steps": steps, "plan": plan,
               "thinking": "".join(thinking), "model": self.prefs.model,
               "stats": {**stats, "seconds": round(time.perf_counter() - t0, 2)}}
        fresh = chats.load(cid) or chat
        fresh["messages"].append(msg)
        chats.save(fresh)
        self.emit("run", chat_id=cid, kind="done", message=msg)
        if self.running_chat == cid:
            self.running_chat = None
        if not self.mouth.speaking and not any(t.is_alive() and t is not threading.current_thread()
                                               for t, _ in self.runs.values()):
            self.set_state("idle")
            if source == "voice" and self.prefs.voice_mode:
                self.listener.converse()

    # ---------------- voice ----------------
    def set_voice_mode(self, on: bool):
        if not self.voice_enabled:
            return
        self.prefs.voice_mode = on
        try:
            if on:
                self.listener.start()
            else:
                self.listener.stop()
                self.interrupt("voice")
        except Exception as e:
            self.prefs.voice_mode = False
            self.emit("toast", level="error", text=f"microphone: {e}")
        self.save_prefs()
        self.emit("prefs", prefs=asdict(self.prefs))

    def _voice_wake(self):
        """ "Hey Forge" (or a double clap): mid-task it stops the task, as Monday does, then listens."""
        if self.running("voice") and not self.approvals:
            self.runs["voice"][1].set()  # signal only: the listener thread must never wait for the task
        self.mouth.interrupt()
        try:
            chime()
        except Exception:
            pass
        self.emit("wake")
        self.set_state("listening")

    def _voice_command(self, text: str):
        if self.voice_chat is None or chats.load(self.voice_chat) is None:
            self.voice_chat = None
        cid = self.send(text, self.voice_chat, source="voice")
        self.voice_chat = cid

    def ptt_key(self, down: bool):
        """Right Option held (from the HUD key monitor): push-to-talk, only while the voice agent is on."""
        if not self.prefs.voice_mode:
            return
        self.ptt_start() if down else self.ptt_stop(None, True)

    def ptt_start(self):
        self.mouth.interrupt()
        try:
            self.ears.start()
        except Exception as e:
            return self.emit("toast", level="error", text=f"microphone: {e}")
        self.set_state("listening")

    def ptt_stop(self, chat_id: str | None = None, voice: bool = False):
        """voice=True (the right Option key): run the words as a spoken command, answered out loud.
        Otherwise (the Studio mic button) the transcript goes back to the composer."""
        if self.state != "listening":
            return
        self.set_state("transcribing")
        try:
            text = self.ears.stop()
        except Exception as e:
            text = ""
            self.emit("toast", level="error", text=f"speech recognition failed: {e}")
        self.set_state("idle")
        if text and voice:
            self._voice_command(text)
        elif text:
            self.emit("transcript", text=text, chat_id=chat_id)
        else:
            self.emit("toast", level="info", text="I didn't catch that.")
