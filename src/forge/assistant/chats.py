"""Conversations saved as JSON files in ~/.forge/chats, newest first. Nothing leaves the machine."""
import json
import re
import threading
import time
import uuid
from pathlib import Path

ROOT = Path.home() / ".forge" / "chats"
_lock = threading.Lock()


def _path(cid: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{12}", cid):
        raise ValueError("bad chat id")
    return ROOT / f"{cid}.json"


def new(title: str = "New chat", mode: str = "computer", workspace: str = "") -> dict:
    """mode: code (works inside the workspace folder) or computer (Mac tasks)."""
    now = time.time()
    return {"id": uuid.uuid4().hex[:12], "title": title, "mode": mode, "workspace": workspace, "created": now,
            "updated": now, "messages": []}


def save(chat: dict):
    ROOT.mkdir(parents=True, exist_ok=True)
    chat["updated"] = time.time()
    with _lock:
        tmp = _path(chat["id"]).with_suffix(".tmp")
        tmp.write_text(json.dumps(chat, ensure_ascii=False))
        tmp.replace(_path(chat["id"]))


def load(cid: str) -> dict | None:
    try:
        return json.loads(_path(cid).read_text())
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        return None


def delete(cid: str):
    _path(cid).unlink(missing_ok=True)


def rename(cid: str, title: str):
    update(cid, title=title)


def update(cid: str, title: str | None = None, mode: str | None = None, workspace: str | None = None):
    c = load(cid)
    if not c:
        return
    if title is not None:
        c["title"] = title.strip()[:80] or c["title"]
    if mode in ("code", "computer"):
        c["mode"] = mode
    if workspace is not None:
        c["workspace"] = workspace
    save(c)


def recent(limit: int = 60) -> list[dict]:
    out = []
    for p in ROOT.glob("*.json") if ROOT.exists() else []:
        try:
            c = json.loads(p.read_text())
            out.append({"id": c["id"], "title": c["title"], "updated": c["updated"], "mode": c.get("mode", "computer"),
                        "workspace": c.get("workspace", "")})
        except (json.JSONDecodeError, KeyError):
            continue
    return sorted(out, key=lambda c: c["updated"], reverse=True)[:limit]


def title_from(text: str) -> str:
    t = " ".join(text.split())
    return t if len(t) <= 48 else t[:46].rsplit(" ", 1)[0] + "…"
