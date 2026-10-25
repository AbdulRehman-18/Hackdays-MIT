"""Files: find with Spotlight, open, read and summarize, safely move to the Trash (always asks first)."""
import subprocess
from pathlib import Path
from typing import Annotated

from pydantic import Field

from .. import runtime
from ..harness.tools import tool
from .mac import osa, q, run

HOME = Path.home()
SKIP = ("/Library/", "/.Trash/", "/node_modules/", "/.git/", "/.venv/", "/site-packages/")
READABLE = {".txt", ".md", ".py", ".js", ".ts", ".json", ".csv", ".html", ".css", ".yaml", ".yml", ".toml", ".swift",
            ".java", ".c", ".cpp", ".go", ".rs", ".sh", ".log", ".rtf", ".tex"}


def spotlight(query: str, limit: int = 8) -> list[Path]:
    """Files whose name, then whose content, matches the query, newest first."""
    q_ = query.replace('"', "").replace("'", "").strip()
    if not q_:
        return []
    out: list[Path] = []
    for expr in (f"kMDItemDisplayName == '*{q_}*'cd", f"kMDItemTextContent == '{q_}'cd"):
        r = run("mdfind", "-onlyin", str(HOME), expr, timeout=10)
        for line in r.stdout.splitlines():
            p = Path(line)
            if any(s in line for s in SKIP) or p in out or p.is_dir() and p.suffix != ".app":
                continue
            out.append(p)
    out.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    return out[:limit]


def resolve(name_or_path: str) -> Path:
    p = Path(name_or_path).expanduser()
    if p.exists():
        return p
    for base in (HOME / "Desktop", HOME / "Documents", HOME / "Downloads"):
        if (base / name_or_path).exists():
            return base / name_or_path
    hits = spotlight(name_or_path, 1)
    if not hits:
        raise FileNotFoundError(f"I couldn't find a file called {name_or_path!r}")
    return hits[0]


def read_text(p: Path, limit: int = 14000) -> str:
    if p.suffix.lower() == ".pdf":
        from pypdf import PdfReader
        text = "\n".join((pg.extract_text() or "") for pg in PdfReader(str(p)).pages[:40])
    elif p.suffix.lower() in (".docx", ".doc", ".rtf", ".pages", ".odt"):
        text = run("textutil", "-convert", "txt", "-stdout", str(p), timeout=20).stdout
    elif p.suffix.lower() in READABLE or p.stat().st_size < 400_000:
        text = p.read_text(errors="ignore")
    else:
        raise ValueError(f"I can't read {p.suffix} files yet")
    return text[:limit]


@tool(group="files")
def find_files(query: Annotated[str, Field(description="words from the file name or its contents")]) -> str:
    """Search the user's files by name or content (Spotlight)."""
    hits = spotlight(query)
    if not hits:
        return f"No files match {query!r}."
    return "Found:\n" + "\n".join(f"- {p.name}  ({p.parent})" for p in hits)


@tool(group="files")
def open_file(name: Annotated[str, Field(description="file name, words from its name, or a full path")]) -> str:
    """Open a file in its default app."""
    p = resolve(name)
    r = subprocess.run(["open", str(p)], capture_output=True, text=True)
    return f"Opened {p.name}." if r.returncode == 0 else f"error: could not open {p.name}"


@tool(group="files")
def read_file(name: Annotated[str, Field(description="file name, words from its name, or a full path")]) -> str:
    """Read a document (PDF, Word, text, code) so you can summarise it or answer questions about it."""
    p = resolve(name)
    text = read_text(p, 12000)
    return f"{p.name} ({p.parent}):\n{text}" if text.strip() else f"{p.name} has no readable text (it may be a scan)."


@tool(group="files")
def summarize_file(name: Annotated[str, Field(description="file name, words from its name, or a full path")],
                   question: Annotated[str, Field(description="what the user wants to know")] = "Summarize it.") -> str:
    """Read a document (PDF, Word, text, code) and answer a question about it."""
    p = resolve(name)
    text = read_text(p)
    if not text.strip():
        return f"{p.name} has no readable text (it may be a scan)."
    return runtime.ask(f"Document: {p.name}\n---\n{text}\n---\n{question}\n"
                       "Answer in at most 4 short sentences that sound natural when spoken. No markdown.")


@tool(group="files", risk="high")
def move_to_trash(name: Annotated[str, Field(description="file name or full path")]) -> str:
    """Move a file to the Trash (it can be restored from there). Always asks the user first."""
    p = resolve(name)
    if not str(p.resolve()).startswith(str(HOME)):
        return "error: I only touch files inside your home folder"
    osa(f"tell application \"Finder\" to delete (POSIX file {q(str(p))} as alias)")
    return f"Moved {p.name} to the Trash."


TOOLS = [find_files, open_file, read_file, summarize_file, move_to_trash]
