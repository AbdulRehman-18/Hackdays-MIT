"""Command line.

forge app                 the assistant in its own window (UI + voice)
forge serve               the same UI in your browser at http://127.0.0.1:8765
forge ask "<request>"     one request through the full brain, printed in the terminal
forge run "<goal>"        the screen agent directly
forge bench               tool-call validity and latency, raw model vs model + Forge
forge setup / doctor      download models and check permissions
forge enroll [--check|--add]  voice lock: record your voice once, test or extend it
forge skills              validate the Agent Skills in skills/
forge export              turn successful traces into a fine-tuning dataset (for Unsloth)"""
import argparse
import statistics
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

from .config import settings
from .harness import Agent
from .harness.backends import BackendError, make_backend


def _printer(kind, d):
    if kind == "plan":
        print("  plan: " + " → ".join(d["steps"]))
    elif kind == "route":
        print(f"  ↳ route: {d['path']}" + (f" ({d.get('tool')})" if d.get("tool") else "")
              + (f" goal={d['goal']!r}" if d.get("goal") else ""))
    elif kind == "action":
        print(f"  {d['step']}. {d['tool']} {d['args']}  — {d['thought']}")
    elif kind == "result":
        print(f"     → {d['result']}")
    elif kind == "repair":
        print(f"     ! repairing: {d['error']}")
    elif kind == "policy" and d["decision"] != "allow":
        print(f"     ⚠ policy: {d['decision']} ({d['reason']})")


def _approve(action, verdict):
    try:
        return input(f"     Allow {action.tool} {action.args}? ({verdict.reason}) [y/N] ").strip().lower() == "y"
    except EOFError:
        return False


def cmd_run(a):
    from .tools import SCREEN
    from .tools.observe import observe
    backend = make_backend(a.route, a.model)
    print(f"[{backend.name}: {backend.model}] {a.goal}")
    agent = Agent(backend, SCREEN, max_steps=a.max_steps, constrained=not a.raw, repair=not a.raw,
                  dry_run=a.dry_run, on_event=_printer, trace_dir=settings.trace_dir,
                  observe=None if a.dry_run else observe, approve=_approve, plan=settings.plan and not a.raw)
    r = agent.run(a.goal)
    print(("✓ " if r.finished else "✗ ") + r.summary)
    return 0 if r.finished else 1


def cmd_ask(a):
    """One message through the same chat agent Forge Studio uses, printed in the terminal."""
    from . import runtime
    from .config import SKILLS_DIR
    from .harness.chat import ChatAgent, Turn
    from .harness.skills import load
    from .tools import CHAT
    backend = make_backend(a.route, a.model or (settings.fast_model if a.route == "local" else None))
    runtime.fast = backend

    def ev(kind, d):
        if kind == "plan" and d["items"]:
            print("  plan: " + " → ".join(f"{i['task']} [{i['tool']}]" for i in d["items"]))
        elif kind == "tool_call":
            print(f"  → {d['tool']} {d['args']}" + ("  (repaired)" if d.get("repaired") else ""))
        elif kind == "tool_result":
            print(f"    ← [{d['status']}] {d['result'][:160]!r}")
        elif kind == "delta":
            print(d["text"], end="", flush=True)
        elif kind == "answer_end":
            print(f"\n  ({d['output_tokens']} tokens, {d['seconds']}s)")
    print(f"[{backend.name}: {backend.model}] {a.request}")
    ChatAgent(backend, CHAT, settings.name, load(SKILLS_DIR), approve=_approve, on_event=ev,
              deep=a.deep, plan=settings.plan).run([], Turn("user", a.request))
    return 0


def _serve(voice: bool, open_browser: bool, voice_agent: bool = False):
    import uvicorn

    from .assistant.server import create_app
    url = f"http://{settings.host}:{settings.port}"
    if open_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    print(f"{settings.name} is running at {url}  (Ctrl+C to quit)")
    uvicorn.run(create_app(voice=voice, voice_agent=voice_agent), host=settings.host, port=settings.port,
                log_level="warning")


def cmd_serve(a):
    _serve(not a.no_voice, not a.no_browser)
    return 0


def _wait_for_server(timeout: float = 20):
    import httpx
    end = time.time() + timeout
    while time.time() < end:
        try:
            httpx.get(f"http://{settings.host}:{settings.port}/api/chats", timeout=1)
            return
        except httpx.HTTPError:
            time.sleep(0.2)


def cmd_app(a):
    """Forge Studio in its own window, with the HUD over the screen (one process, one session)."""
    base = f"http://{settings.host}:{settings.port}"
    threading.Thread(target=_serve, args=(not a.no_voice, False), daemon=True).start()
    _wait_for_server()
    from .assistant import hud_native
    try:
        import webview
    except ImportError:
        print("The native window needs pywebview (uv sync --extra app). Opening the browser instead.")
        webbrowser.open(base)
        hud_native.run()
        return 0

    if sys.platform == "win32":  # the HUD is a second, small window next to Studio (no AppKit here)
        from .assistant import hud_win
        studio = webview.create_window(f"{settings.name} Studio", base, width=1360, height=880, min_size=(760, 560),
                                       background_color="#07090d")
        hud = webview.create_window(**hud_win.window_args())
        studio.events.closed += lambda: hud.destroy()  # the HUD must not keep the app alive

        def add_win_hud():
            hud_win.attach(hud, hud_native.wait_for_session())
        webview.start(add_win_hud)
        return 0

    def add_hud():
        from PyObjCTools import AppHelper
        session = hud_native.wait_for_session()
        AppHelper.callAfter(hud_native.create, session, session.prefs.hud_mode)
    webview.create_window(f"{settings.name} Studio", base, width=1360, height=880, min_size=(760, 560),
                          background_color="#07090d")
    webview.start(add_hud)
    return 0


def cmd_voice(a):
    """The voice agent: the Monday program itself (wake word, HUD, voice), on Gemma. Ctrl+C stops it."""
    import os

    from .config import MONDAY_DIR, monday_command
    cmd = monday_command()
    if cmd is None:
        print(f"Monday is not at {MONDAY_DIR}; set MONDAY_DIR in .env. Starting the built-in voice agent instead.")
        threading.Thread(target=_serve, args=(True, False, True), daemon=True).start()
        _wait_for_server()
        from .assistant import hud_native
        hud_native.run()
        return 0
    os.chdir(MONDAY_DIR)
    os.execv(cmd[0], [cmd[0], *cmd[1:], *a.args])


PLIST = Path.home() / "Library" / "LaunchAgents" / "dev.monday.assistant.plist"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _login_windows(action: str) -> int:
    """Start the voice agent when you sign in to Windows: a per-user Run entry, no admin rights needed."""
    import winreg
    if action == "uninstall":
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, "Monday")
        except FileNotFoundError:
            pass
        print("Monday will no longer start at login.")
        return 0
    py = Path(sys.executable)
    pyw = py.with_name("pythonw.exe")  # no console window
    cmd = f'"{pyw if pyw.exists() else py}" -m forge voice'
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
        winreg.SetValueEx(k, "Monday", 0, winreg.REG_SZ, cmd)
    print(f"Monday will start at login (voice mode): {cmd}")
    print("Undo with: monday login uninstall")
    return 0


def cmd_login(a):
    """Start Forge's voice assistant when you log in (a per-user LaunchAgent), or stop doing so."""
    if sys.platform == "win32":
        return _login_windows(a.action)
    if a.action == "uninstall":
        subprocess.run(["launchctl", "unload", str(PLIST)], capture_output=True)
        PLIST.unlink(missing_ok=True)
        print("Monday will no longer start at login.")
        return 0
    log = Path.home() / ".forge" / "forge.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[2]
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    PLIST.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>dev.monday.assistant</string>
  <key>ProgramArguments</key><array><string>{sys.executable}</string><string>-m</string><string>forge</string><string>voice</string></array>
  <key>WorkingDirectory</key><string>{root}</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
</dict></plist>
""")
    subprocess.run(["launchctl", "unload", str(PLIST)], capture_output=True)
    subprocess.run(["launchctl", "load", str(PLIST)], check=False)
    print(f"Monday will start at login (voice mode). Logs: {log}. Undo with: monday login uninstall")
    return 0


def cmd_setup(a):
    from .assistant.voice import download_voice
    models = sorted({settings.fast_model, settings.agent_model})
    for m in models:
        print(f"→ ollama pull {m}")
        subprocess.run(["ollama", "pull", m], check=False)
    print("→ voice model (Kokoro-82M)")
    download_voice()
    print("Done. Now run: forge doctor")
    return 0


def _doctor_windows(line):
    """What the voice agent needs on Windows: a microphone Windows lets us use, a speaker, WebView2 for the HUD."""
    try:
        import sounddevice as sd
        mic, spk = sd.query_devices(kind="input"), sd.query_devices(kind="output")
        line(True, f"microphone: {mic['name']}")
        line(True, f"speaker: {spk['name']}")
        import numpy as np
        with sd.InputStream(samplerate=16000, channels=1, dtype="float32") as s:
            audio, _ = s.read(3200)
        line(bool(np.isfinite(audio).all()), "the microphone can be opened",
             "Settings > Privacy & security > Microphone: allow desktop apps to use the microphone")
    except Exception as e:
        line(False, f"microphone: {str(e)[:100]}",
             "Settings > Privacy & security > Microphone: allow desktop apps; check the default input device")
    try:
        import winreg
        clients = r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
        winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, clients).Close()
        line(True, "WebView2 runtime (the HUD and the Studio window)")
    except OSError:
        line(False, "WebView2 runtime (the HUD and the Studio window)",
             "install the Evergreen runtime from https://developer.microsoft.com/microsoft-edge/webview2")
    try:
        import webview  # noqa: F401
        line(True, "pywebview (the HUD window)")
    except ImportError:
        line(False, "pywebview (the HUD window)", "uv sync --extra app")


def cmd_doctor(a):
    import httpx

    from .assistant.voice import KOKORO_FILES, MODELS
    from .tools.observe import permissions
    ok = True

    def line(good, text, fix=""):
        nonlocal ok
        ok &= good
        sym = "  ✓ " if good else "  ✗ "
        try:
            print(sym + text + ("" if good or not fix else f"\n      fix: {fix}"))
        except UnicodeEncodeError:
            sym_fallback = "  [+] " if good else "  [-] "
            print(sym_fallback + text + ("" if good or not fix else f"\n      fix: {fix}"))
    from .config import HARDWARE, PROFILE
    from .hardware import fits
    gpu = f", ~{HARDWARE.gpu_budget_gb:g} GB for models on the GPU" if HARDWARE.apple_silicon else (
        "" if sys.platform == "win32" else ", no Apple GPU")
    print(f"  · {HARDWARE.chip}, {HARDWARE.ram_gb:g} GB{gpu}\n    {PROFILE.note}")
    if settings.route == "openai":
        from .harness.backends.openai import OpenAIBackend
        try:
            ids = OpenAIBackend("", settings.openai_url, settings.openai_key).list_models()
            line(True, f"OpenAI-compatible server at {settings.openai_url}")
            line(settings.openai_model in ids, f"model {settings.openai_model}",
                 f"load it in your server, or set FORGE_OPENAI_MODEL to one of: {', '.join(ids[:6])}")
        except Exception:
            line(False, f"no OpenAI-compatible server at {settings.openai_url}",
                 "start llama-server, LM Studio, mlx_lm.server or Ollama, or fix FORGE_OPENAI_URL")
    else:
        try:
            models = httpx.get(f"{settings.ollama_url}/api/tags", timeout=3).json()["models"]
            sizes = {m["name"]: m.get("size", 0) / 1e9 for m in models}
            line(True, f"Ollama is running at {settings.ollama_url}")
            for m in sorted({settings.fast_model, settings.agent_model}):
                line(m in sizes, f"model {m}", f"ollama pull {m}")
                if m in sizes and HARDWARE.apple_silicon and not fits(sizes[m], HARDWARE):
                    line(False, f"{m} ({sizes[m]:.1f} GB) is too big for this Mac's GPU memory",
                         f"use {PROFILE.fast_model} (set FORGE_FAST_MODEL / FORGE_AGENT_MODEL)")
            if HARDWARE.apple_silicon and settings.fast_model != settings.agent_model and all(m in sizes for m in
                                                                   (settings.fast_model, settings.agent_model)):
                both = sizes[settings.fast_model] + sizes[settings.agent_model]
                line(fits(both, HARDWARE), "chat and screen-agent models fit in memory together",
                     f"they need {both:.1f} GB, so Ollama swaps them (slow): set FORGE_AGENT_MODEL to "
                     f"{settings.fast_model}")
        except Exception:
            line(False, "Ollama is not running", "ollama serve")
    print(("  ✓ " if settings.gemini_key else "  · ") + "AI Studio key (optional, for the hosted route)"
          + ("" if settings.gemini_key else ": add GEMINI_API_KEY to .env to use it"))
    if sys.platform == "win32":
        _doctor_windows(line)
        line(all((MODELS / f).exists() for f in KOKORO_FILES), "Kokoro voice (otherwise the Windows voice is used)",
             "monday setup")
        return 0 if ok else 1
    p = permissions()
    line(p["accessibility"], "Accessibility permission (clicking real controls)",
         "System Settings > Privacy & Security > Accessibility: add your terminal app, then restart it")
    line(p["screen_recording"], "Screen Recording permission (seeing the screen)",
         "System Settings > Privacy & Security > Screen Recording: add your terminal app, then restart it")
    line(all((MODELS / f).exists() for f in KOKORO_FILES), "Kokoro voice (otherwise the macOS voice is used)",
         "forge setup")
    return 0 if ok else 1


def cmd_enroll(a):
    from .assistant import enroll
    try:
        return enroll.main("check" if a.check else "add" if a.add else "")
    except KeyboardInterrupt:
        print("\nCancelled.")
        return 130


def cmd_skills(a):
    from .config import SKILLS_DIR
    from .harness.skills import validate
    bad = 0
    for d in sorted(p for p in SKILLS_DIR.glob("*") if p.is_dir()):
        problems = validate(d)
        bad += bool(problems)
        print(("  ✓ " if not problems else "  ✗ ") + d.name + "".join(f"\n      {p}" for p in problems))
    return 1 if bad else 0


def cmd_export(a):
    from .training import export
    n, path = export(Path(settings.trace_dir), Path(a.out))
    print(f"wrote {n} examples to {path}")
    return 0


# ---- benchmark ----
BENCH = [
    ("open calculator", "open_app"), ("launch the Notes app", "open_app"), ("can you open safari for me", "open_app"),
    ("open the browser", "open_app"), ("start Terminal", "open_app"), ("open System Settings", "open_app"),
    ("go to youtube.com", "open_url"), ("open github.com in the browser", "open_url"),
    ("take me to the wikipedia page about Alan Turing", "open_url"), ("show me google maps", "open_url"),
    ("what is 12 times 12?", "done"), ("say hi", "done"), ("open Finder", "open_app"),
    ("open the music app", "open_app"), ("I need to check my email in the Mail app", "open_app"),
    ("open https://news.ycombinator.com", "open_url"), ("open the App Store", "open_app"),
    ("what's the capital of France?", "done"), ("visit python.org", "open_url"), ("open Preview", "open_app"),
]


def _pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, round(p / 100 * (len(xs) - 1)))] if xs else 0.0


def bench(backend, mode: str, runs: int, temperature: float, tools, tasks=BENCH):
    forge = mode == "forge"
    stats = {"calls": 0, "valid_first": 0, "valid_final": 0, "right_tool": 0, "secs": [], "tps": []}
    for goal, want in (tasks * (runs // len(tasks) + 1))[:runs]:
        times, tps = [], []

        def ev(kind, d, times=times, tps=tps):
            if kind == "model":
                times.append(d["seconds"])
                if d["tokens"] and d["seconds"]:
                    tps.append(d["tokens"] / d["seconds"])
        r = Agent(backend, tools, max_steps=1, constrained=forge, repair=forge, dry_run=True,
                  temperature=temperature, on_event=ev).run(goal)
        s = r.steps[0] if r.steps else None
        ok = bool(s and s.action)
        stats["calls"] += 1
        stats["valid_first"] += ok and s.attempts == 1
        stats["valid_final"] += ok
        stats["right_tool"] += ok and s.action.tool == want
        stats["secs"].append(sum(times))
        stats["tps"].extend(tps)
        mark = "✓" if ok and s.action.tool == want else ("~" if ok else "✗")
        got = s.action.tool if ok else (s.result[:70] if s else "-")
        print(f"  {mark} {goal!r}: {got} ({sum(times):.1f}s{', repaired' if ok and s.attempts > 1 else ''})")
    return stats


def bench_native(backend, runs: int, temperature: float, tools, tasks=BENCH):
    """The usual way to run Gemma as an agent: Ollama's built-in tool calling, a plain system prompt, no repair."""
    from pydantic import ValidationError

    from .harness.backends import Message
    specs = [{"type": "function", "function": {"name": t.name, "description": t.description,
                                               "parameters": t.args_schema()}} for t in tools]
    by_name = {t.name: t for t in tools}
    stats = {"calls": 0, "valid_first": 0, "valid_final": 0, "right_tool": 0, "secs": [], "tps": []}
    for goal, want in (tasks * (runs // len(tasks) + 1))[:runs]:
        msgs = [Message("system", "You are an assistant that operates the user's Mac. Use a tool when one fits, "
                                  "otherwise answer directly."), Message("user", goal)]
        try:
            msg, secs = backend.native_tools(msgs, specs, temperature)
        except BackendError as e:
            msg, secs = {"content": f"error: {e}"}, 0.0
        calls = msg.get("tool_calls") or []
        got, ok = "-", False
        if calls:
            fn = calls[0].get("function", {})
            got = fn.get("name", "?")
            try:
                ok = got in by_name and bool(by_name[got].args.model_validate(fn.get("arguments") or {}))
            except ValidationError:
                ok = False
        elif (msg.get("content") or "").strip() and not msg["content"].startswith("error"):
            got, ok = "done", True  # answered in plain text: the native equivalent of done
        stats["calls"] += 1
        stats["valid_first"] += ok
        stats["valid_final"] += ok
        stats["right_tool"] += ok and got == want
        stats["secs"].append(secs)
        mark = "✓" if ok and got == want else ("~" if ok else "✗")
        print(f"  {mark} {goal!r}: {got}{'' if ok else ' (invalid)'} ({secs:.1f}s)")
    return stats


def cmd_bench(a):
    from .tools import QUICK
    from .tools.apps import open_app
    from .tools.web import open_url
    backend = make_backend(a.route, a.model)
    tools = QUICK if a.tools == "all" else [open_app, open_url]
    rows = []
    modes = {"both": ["raw", "forge"], "all": ["native", "raw", "forge"]}.get(a.mode, [a.mode])
    for mode in modes:
        print(f"\n== {mode}: {backend.name} {backend.model}, {a.runs} tasks, {len(tools) + 1} tools ==")
        if mode == "native" and not hasattr(backend, "native_tools"):
            print("  (native tool calling is only measured on the local route)")
            continue
        s = (bench_native(backend, a.runs, a.temperature, tools) if mode == "native"
             else bench(backend, mode, a.runs, a.temperature, tools))
        n = s["calls"]
        rows.append((mode, f"{s['valid_first'] / n:.0%}", f"{s['valid_final'] / n:.0%}", f"{s['right_tool'] / n:.0%}",
                     f"{statistics.median(s['secs']):.2f}s", f"{_pct(s['secs'], 90):.2f}s",
                     f"{statistics.median(s['tps']):.0f}" if s["tps"] else "-"))
    head = ("mode", "valid 1st try", "valid final", "right tool", "p50 step", "p90 step", "tok/s")
    table = "| " + " | ".join(head) + " |\n|" + "---|" * len(head) + "\n"
    table += "\n".join("| " + " | ".join(r) + " |" for r in rows)
    print(f"\n{backend.name} · {backend.model} · {len(tools) + 1} tools\n{table}")
    if a.out:
        with open(a.out, "a") as f:
            f.write(f"\n### {backend.name} · `{backend.model}` · {a.runs} tasks · {len(tools) + 1} tools offered\n\n"
                    f"{table}\n")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="monday", description="Monday: your assistant on open-weight Gemma, on this Mac.")
    sub = p.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--route", default=settings.route, choices=["local", "openai", "aistudio"])
    common.add_argument("--model", help="override the model for this route")
    for name in ("app", "serve"):
        s = sub.add_parser(name, help="start the assistant" + (" in a native window" if name == "app" else " UI"))
        s.add_argument("--no-voice", action="store_true", help="text only, no speech output")
        if name == "serve":
            s.add_argument("--no-browser", action="store_true")
    q = sub.add_parser("ask", parents=[common], help="one message through the chat agent, in the terminal")
    q.add_argument("request")
    q.add_argument("--deep", action="store_true", help="deep research mode")
    r = sub.add_parser("run", parents=[common], help="run the screen agent on a goal")
    r.add_argument("goal")
    r.add_argument("--max-steps", type=int, default=settings.max_steps)
    r.add_argument("--raw", action="store_true", help="no schema enforcement or repair (the bare model)")
    r.add_argument("--dry-run", action="store_true", help="decide actions but do not execute them")
    b = sub.add_parser("bench", parents=[common], help="measure tool-call validity and latency")
    b.add_argument("--runs", type=int, default=20)
    b.add_argument("--mode", default="all", choices=["native", "raw", "forge", "both", "all"],
                   help="native: Ollama tool calling; raw: Forge prompt without schema/repair; forge: everything")
    b.add_argument("--tools", default="all", choices=["all", "few"], help="offer all quick tools, or just two")
    b.add_argument("--temperature", type=float, default=0.2)
    b.add_argument("--out", help="append the results table to this Markdown file")
    v = sub.add_parser("voice", help="the Monday voice agent: wake word, HUD and voice (on Gemma)")
    v.add_argument("args", nargs=argparse.REMAINDER, help="passed to Monday, e.g. --ptt or --text")
    lg = sub.add_parser("login", help="start the voice assistant when you log in")
    lg.add_argument("action", choices=["install", "uninstall"])
    sub.add_parser("setup", help="download the Gemma models and the voice")
    sub.add_parser("doctor", help="check models, keys and macOS permissions")
    en = sub.add_parser("enroll", help="voice lock: only your voice can command Forge")
    g = en.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true", help="test whether Forge recognises you in this room")
    g.add_argument("--add", action="store_true", help="add clips from this (noisy) room to your voiceprint")
    sub.add_parser("skills", help="validate the Agent Skills")
    e = sub.add_parser("export", help="export successful traces as a fine-tuning dataset")
    e.add_argument("--out", default="training/data/forge_traces.jsonl")
    a = p.parse_args(argv)
    cmds = {"app": cmd_app, "voice": cmd_voice, "login": cmd_login,
            "serve": cmd_serve, "ask": cmd_ask, "run": cmd_run, "bench": cmd_bench,
            "setup": cmd_setup, "doctor": cmd_doctor, "enroll": cmd_enroll, "skills": cmd_skills, "export": cmd_export}
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    try:
        return cmds[a.cmd](a)
    except BackendError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
