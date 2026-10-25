"""Small macOS helpers shared by the tools: AppleScript, the front app, notifications."""
import os
import re
import subprocess
import sys
import time

# what people say -> what Windows can start (App Paths and PATH know these names)
WIN_APPS = {"calculator": "calc", "notes": "notepad", "notepad": "notepad", "terminal": "wt", "paint": "mspaint",
            "browser": "msedge", "chrome": "chrome", "google chrome": "chrome", "edge": "msedge", "firefox": "firefox",
            "file explorer": "explorer", "finder": "explorer", "files": "explorer", "settings": "ms-settings:",
            "system settings": "ms-settings:", "task manager": "taskmgr", "word": "winword", "excel": "excel",
            "powerpoint": "powerpnt", "vs code": "code", "vscode": "code", "spotify": "spotify"}


def _win_open(args: tuple) -> subprocess.CompletedProcess:
    """macOS `open [-g] [-a App] [target]` on Windows: a file or URL opens in its default app, -a starts an app."""
    rest = [a for a in args[1:] if a != "-g"]
    try:
        if rest and rest[0] == "-a" and len(rest) >= 2:
            name = WIN_APPS.get(rest[1].strip().lower(), rest[1])
            script = f"Start-Process -FilePath {_ps(name)}"
            r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                               capture_output=True, text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
            return subprocess.CompletedProcess(args, r.returncode, r.stdout, r.stderr)
        os.startfile(rest[0])  # noqa: S606  (the default app for this file or web address)
        return subprocess.CompletedProcess(args, 0, "", "")
    except OSError as e:
        return subprocess.CompletedProcess(args, 1, "", str(e))


def _ps(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def run(*cmd: str, timeout: float = 15) -> subprocess.CompletedProcess:
    if sys.platform == "win32" and cmd and cmd[0] == "open":
        return _win_open(cmd)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def osa(script: str, timeout: float = 20) -> str:
    """Run AppleScript and return its output. Raises RuntimeError with a readable message on failure."""
    r = run("osascript", "-e", script, timeout=timeout)
    if r.returncode != 0:
        err = r.stderr.strip()
        app = re.search(r'application "([^"]+)"', script)
        if app and ("(-609)" in err or "(-600)" in err):  # the app was not running yet: launch it and retry once
            run("open", "-g", "-a", app.group(1))
            time.sleep(2)
            r = run("osascript", "-e", script, timeout=timeout)
            if r.returncode == 0:
                return r.stdout.strip()
            err = r.stderr.strip()
        if "-1743" in err or "not allowed" in err.lower() or "(-609)" in err:
            who = app.group(1) if app else "that app"
            raise RuntimeError(f"macOS didn't let me control {who}. Allow it under System Settings > "
                               "Privacy & Security > Automation")
        raise RuntimeError(re.sub(r"^\d+:\d+: execution error: ", "", err).rstrip(". ") or "AppleScript failed")
    return r.stdout.strip()


def q(text: str) -> str:
    """Quote text for an AppleScript string literal."""
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


def front_app() -> str:
    """Name of the frontmost app, read without needing any extra permission."""
    if sys.platform == "win32":
        return ""
    asn = run("lsappinfo", "front").stdout.strip()
    out = run("lsappinfo", "info", "-only", "name", asn).stdout
    m = re.search(r'"(?:LSDisplayName|name)"\s*=\s*"([^"]+)"', out)
    return m.group(1) if m else ""


def notify(title: str, text: str):
    try:
        osa(f"display notification {q(text)} with title {q(title)}")
    except Exception:
        pass
