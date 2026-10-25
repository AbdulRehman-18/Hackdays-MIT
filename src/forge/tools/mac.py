"""Small macOS helpers shared by the tools: AppleScript, the front app, notifications."""
import re
import subprocess
import time


def run(*cmd: str, timeout: float = 15) -> subprocess.CompletedProcess:
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
    asn = run("lsappinfo", "front").stdout.strip()
    out = run("lsappinfo", "info", "-only", "name", asn).stdout
    m = re.search(r'"(?:LSDisplayName|name)"\s*=\s*"([^"]+)"', out)
    return m.group(1) if m else ""


def notify(title: str, text: str):
    try:
        osa(f"display notification {q(text)} with title {q(title)}")
    except Exception:
        pass
