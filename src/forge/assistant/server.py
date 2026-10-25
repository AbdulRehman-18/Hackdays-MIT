"""Local server (127.0.0.1 only): Forge Studio at /, the HUD at /hud, a REST API and one event WebSocket.

WebSocket, client -> server:
  {"type": "send", "chat_id", "text", "attachments": [upload ids], "options": {search, computer, think, approve}}
  {"type": "approve", "id", "allow"}   {"type": "cancel"}   {"type": "ptt", "down": bool, "chat_id"}
  {"type": "key", "key": "esc" | "space" | "toggle_hud"}      (from the HUD's global key monitor)
WebSocket, server -> client: state, run (start/tool_call/tool_result/delta/thinking/answer_end/done/...),
  user_message, chat_created, approval, approval_done, level, caption, cursor, click, model, prefs, pull, toast"""
import asyncio
import json
import threading
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from ..config import HARDWARE, PROFILE, SKILLS_DIR, settings
from . import chats
from .session import Session

STATIC = Path(__file__).with_name("static")
ROOT = Path(__file__).resolve().parents[3]
session: Session | None = None


def warm_up(s: Session):
    """Load the model and Whisper in the background so the first message is quick."""
    try:
        if not s.prefs.model.startswith(("aistudio:", "openai:")):
            httpx.post(f"{s.s.ollama_url}/api/generate", json={"model": s.prefs.model, "keep_alive": s.s.keep_alive},
                       timeout=180)
            s.emit("model", **s.model_info())
        if s.voice_enabled:
            from .voice import preload
            preload()
    except Exception as e:
        s.emit("toast", level="error", text=f"warm-up: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global session
    session = Session(settings, voice=app.state.voice)
    if app.state.voice_agent:
        session.set_voice_mode(True)
    threading.Thread(target=warm_up, args=(session,), daemon=True).start()
    yield
    session.interrupt()
    session.listener.stop()


def create_app(voice: bool = True, voice_agent: bool = False) -> FastAPI:
    """voice: speech output and push-to-talk are available. voice_agent: start always-on listening right away."""
    app = FastAPI(title="Forge", lifespan=lifespan)
    app.state.voice, app.state.voice_agent = voice, voice_agent

    @app.get("/")
    def studio():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/hud")
    def hud():
        return FileResponse(STATIC / "hud.html", headers={"Cache-Control": "no-cache"})

    @app.get("/static/{name}")
    def static(name: str):
        p = (STATIC / name).resolve()
        if p.parent != STATIC.resolve() or not p.exists():
            raise HTTPException(404)
        return FileResponse(p, headers={"Cache-Control": "no-cache"})

    @app.get("/api/status")
    def status():
        from ..tools.observe import permissions
        from .voice import KOKORO_FILES, MODELS
        return {"state": session.state, "name": settings.name, "model": session.model_info(),
                "prefs": asdict(session.prefs), "permissions": permissions(), "voice": session.voice_enabled,
                "kokoro": all((MODELS / f).exists() for f in KOKORO_FILES), "has_key": bool(settings.gemini_key),
                "hardware": {"chip": HARDWARE.chip, "ram_gb": HARDWARE.ram_gb, "tier": HARDWARE.tier,
                             "note": PROFILE.note}}

    @app.get("/api/chats")
    def list_chats():
        return chats.recent()

    @app.get("/api/chats/{cid}")
    def get_chat(cid: str):
        c = chats.load(cid)
        if not c:
            raise HTTPException(404)
        return c

    @app.delete("/api/chats/{cid}")
    def delete_chat(cid: str):
        chats.delete(cid)
        return {"ok": True}

    @app.patch("/api/chats/{cid}")
    def update_chat(cid: str, body: dict):
        ws = body.get("workspace")
        if ws:
            ws = str(Path(str(ws)).expanduser())
            if not Path(ws).is_dir():
                raise HTTPException(400, f"{ws} is not a folder")
        chats.update(cid, title=body.get("title"), mode=body.get("mode"), workspace=ws)
        return chats.load(cid) or {}

    # ---- workspaces (Code mode) ----
    def _ws(path: str) -> dict:
        from ..tools.code import branch
        p = Path(path).expanduser().resolve()
        if not p.is_dir():
            return {"ok": False, "error": f"{path} is not a folder"}
        return {"ok": True, "path": str(p), "name": p.name, "branch": branch(p)}

    @app.get("/api/workspaces")
    def workspaces():
        return {"recent": session.workspaces()}

    @app.post("/api/workspaces/check")
    def check_workspace(body: dict):
        return _ws(str(body.get("path", "")))

    @app.post("/api/workspaces/pick")
    def pick_workspace():
        """The macOS folder chooser."""
        import subprocess
        r = subprocess.run(["osascript", "-e", 'POSIX path of (choose folder with prompt "Open a project in Forge")'],
                           capture_output=True, text=True, timeout=600)
        if r.returncode or not r.stdout.strip():
            return {"cancelled": True}
        return _ws(r.stdout.strip().rstrip("/") or "/")

    @app.get("/api/workspaces/files")
    def workspace_files(path: str, q: str = ""):
        from ..tools.code import find
        p = Path(path).expanduser().resolve()
        if not p.is_dir():
            raise HTTPException(400, "not a folder")
        return {"files": find(p, q)}

    @app.get("/api/models")
    def models():
        return session.models()

    @app.post("/api/models/select")
    def select(body: dict):
        session.set_prefs(model=str(body["model"]))
        return session.model_info()

    @app.post("/api/models/pull")
    def pull(body: dict):
        session.pull(str(body["model"]))
        return {"ok": True}

    @app.post("/api/prefs")
    def prefs(body: dict):
        session.set_prefs(**body)
        return asdict(session.prefs)

    @app.post("/api/key")
    def key(body: dict):
        handle({"type": "key", "key": str(body.get("key", ""))})
        return {"ok": True}

    @app.post("/api/voice-agent")
    def voice_agent(body: dict):
        """Continue with the voice agent (on) or go back to Studio only (off)."""
        if not session.voice_enabled:
            raise HTTPException(400, "voice is off (started with --no-voice)")
        session.set_voice_mode(bool(body.get("on", True)))
        return {"voice_mode": session.prefs.voice_mode, "hud": session.hud_on}

    @app.post("/api/speak")
    def speak(body: dict):
        if not session.voice_enabled:
            raise HTTPException(400, "voice is off (started with --no-voice)")
        session.mouth.interrupt()
        session.mouth.say(str(body.get("text", ""))[:4000])
        return {"ok": True}

    @app.post("/api/upload")
    async def upload(file: UploadFile):
        data = await file.read()
        if len(data) > 25 * 1024 * 1024:
            raise HTTPException(413, "files up to 25 MB")
        return session.add_upload(file.filename or "file", data, file.content_type or "")

    @app.get("/api/uploads/{uid}")
    def get_upload(uid: str):
        u = session.uploads.get(uid)
        if not u:
            raise HTTPException(404)
        return FileResponse(u["path"])

    @app.get("/api/skills")
    def skills():
        from ..harness.skills import parse_frontmatter, validate
        out = []
        for d in sorted(p for p in SKILLS_DIR.glob("*") if p.is_dir()):
            text = (d / "SKILL.md").read_text() if (d / "SKILL.md").exists() else ""
            fm = parse_frontmatter(text) if text.startswith("---") else {}
            files = sorted(str(f.relative_to(d)) for f in d.rglob("*") if f.is_file() and f.name != "SKILL.md")
            out.append({"name": fm.get("name", d.name), "description": fm.get("description", ""),
                        "problems": validate(d), "body": text.split("---", 2)[-1].strip() if text else "",
                        "files": files, "license": fm.get("license", "")})
        return out

    @app.get("/api/train")
    def train():
        traces = Path(settings.trace_dir)
        files = list(traces.glob("*.jsonl")) if traces.exists() else []
        samples = finished = 0
        for f in files:
            lines = f.read_text().splitlines()
            samples += sum('"kind": "sample"' in line for line in lines)
            finished += any('"kind": "end"' in line and '"finished": true' in line for line in lines)
        data = ROOT / "training" / "data" / "forge_traces.jsonl"
        return {"traces": len(files), "samples": samples, "finished": finished,
                "dataset": sum(1 for _ in data.open()) if data.exists() else 0, "dataset_path": str(data)}

    @app.post("/api/train/export")
    def train_export():
        from ..training import export
        n, path = export(Path(settings.trace_dir), ROOT / "training" / "data" / "forge_traces.jsonl")
        return {"examples": n, "path": str(path)}

    @app.get("/api/evals")
    def evals():
        p = ROOT / "evals" / "results.md"
        return {"markdown": p.read_text() if p.exists() else ""}

    @app.post("/api/evals/run")
    def run_eval(body: dict):
        threading.Thread(target=_bench, args=(str(body.get("model") or session.prefs.model),
                                              int(body.get("runs", 20))), daemon=True).start()
        return {"ok": True}

    @app.websocket("/ws")
    async def ws(sock: WebSocket):
        await sock.accept()
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue(maxsize=5000)

        def listener(msg: dict):
            loop.call_soon_threadsafe(lambda: None if q.full() else q.put_nowait(msg))
        session.listeners.append(listener)
        await sock.send_json({"type": "hello", "state": session.state, "name": settings.name,
                              "model": session.model_info(), "prefs": asdict(session.prefs),
                              "running_chat": session.running_chat, "voice": session.voice_enabled})

        async def pump():
            while True:
                await sock.send_json(await q.get())
        sender = asyncio.create_task(pump())
        try:
            while True:
                msg = json.loads(await sock.receive_text())
                await asyncio.to_thread(handle, msg)
        except WebSocketDisconnect:
            pass
        finally:
            sender.cancel()
            session.listeners.remove(listener)
    return app


def _bench(model: str, runs: int):
    """Run the benchmark from the Evals page, streaming progress as toasts and appending to evals/results.md."""
    import contextlib
    import io

    from ..cli import main
    session.emit("eval", status="start", model=model)
    buf = io.StringIO()
    args = ["bench", "--runs", str(runs), "--out", str(ROOT / "evals" / "results.md")]
    if model.startswith(("aistudio:", "openai:")):
        route, name = model.split(":", 1)
        args += ["--route", route, "--model", name, "--mode", "both"]
    else:
        args += ["--model", model]
    with contextlib.redirect_stdout(buf):
        try:
            main(args)
        except SystemExit:
            pass
    session.emit("eval", status="done", model=model, log=buf.getvalue()[-4000:])


def handle(msg: dict):
    kind = msg.get("type")
    if kind == "send":
        session.send(str(msg.get("text", "")), msg.get("chat_id"), msg.get("attachments") or [],
                     options=msg.get("options"))
    elif kind == "approve":
        session.answer_approval(str(msg.get("id")), bool(msg.get("allow")), bool(msg.get("always")))
    elif kind == "cancel":
        session.interrupt(msg.get("slot", "text"))
    elif kind == "ptt":
        if msg.get("down"):
            session.ptt_start()
        else:
            session.ptt_stop(msg.get("chat_id"))
    elif kind == "key":
        key = msg.get("key")
        if key == "esc":
            session.escape()
        elif key == "space":
            session.space_tap()
        elif key == "ptt_down":
            session.ptt_start()
        elif key == "ptt_up":
            session.ptt_stop(voice=True)
        elif key == "toggle_hud":
            session.toggle_hud()
