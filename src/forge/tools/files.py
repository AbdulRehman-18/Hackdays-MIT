"""Files: find with Spotlight, open, read and summarize, safely move to the Trash (always asks first)."""
import re
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


DOCS = {".pdf", ".doc", ".docx", ".pages", ".rtf", ".txt", ".md", ".key", ".pptx", ".odt", ".epub", ".xlsx", ".csv",
        ".numbers"}
FILLER = {"a", "an", "the", "my", "me", "of", "for", "to", "in", "on", "and", "or", "file", "files", "document",
          "documents", "doc", "find", "open", "search", "show", "named", "called", "about", "book", "ebook", "copy"}
KINDS = {"pdf": (".pdf",), "pdfs": (".pdf",), "word": (".docx", ".doc"), "docx": (".docx",),
         "excel": (".xlsx", ".xls", ".csv"),
         "spreadsheet": (".xlsx", ".xls", ".csv", ".numbers"), "image": (".png", ".jpg", ".jpeg", ".heic", ".webp"),
         "photo": (".png", ".jpg", ".jpeg", ".heic"), "picture": (".png", ".jpg", ".jpeg", ".heic"),
         "slides": (".pptx", ".key", ".pdf"), "presentation": (".pptx", ".key", ".pdf"), "resume": (), "cv": ()}


def _mdfind(expr: str) -> list[str]:
    return run("mdfind", "-onlyin", str(HOME), expr, timeout=10).stdout.splitlines()


def spotlight(query: str, limit: int = 8) -> list[Path]:
    """Files matching the query's words (all of them in the name or the contents), newest first. "pdf", "word",
    "image"... filter by type, and filler ("my", "the", "file") is ignored. With no full match, the files that
    match the most words in their name are returned."""
    raw = [w for w in re.findall(r"[\w.+#-]+", query.lower()) if w]
    exts = tuple(e for w in raw for e in KINDS.get(w, ()))
    words = [w for w in raw if w not in FILLER and w not in KINDS or w in ("resume", "cv")]
    if not words:
        words = [w for w in raw if w not in FILLER]
    if not words:
        return []
    esc = [w.replace("'", "").replace('"', "") for w in words]
    exprs = [" && ".join(f"kMDItemDisplayName == '*{w}*'cd" for w in esc),
             " && ".join(f"kMDItemTextContent == '{w}'cd" for w in esc)]
    def ok(line: str, content: bool) -> bool:
        p = Path(line)
        if any(s in line for s in SKIP) or p.is_dir() and p.suffix != ".app":
            return False
        if exts:
            return p.suffix.lower() in exts
        return not content or p.suffix.lower() in DOCS  # a word inside source code is not "my resume"

    def newest(lines: list[str], content: bool = False) -> list[Path]:
        ps = {Path(x) for x in lines if ok(x, content)}
        return sorted(ps, key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)

    out = newest(_mdfind(exprs[0]))  # the name matches every word: the best hits, first
    if not out and len(esc) > 1:  # no name has every word: names with most of them
        counts: dict[str, int] = {}
        for w in esc:
            for line in _mdfind(f"kMDItemDisplayName == '*{w}*'cd")[:300]:
                counts[line] = counts.get(line, 0) + 1
        need = max(2, (len(esc) + 1) // 2)
        out = [p for p in newest([x for x, n in counts.items() if n >= need])]
        out.sort(key=lambda p: -counts.get(str(p), 0))
    out += [p for p in newest(_mdfind(exprs[1]), content=True) if p not in out]  # then files that mention it
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
    """Find files on this Mac (documents, PDFs, books, resumes, slides, images) by words from their name or
    contents. Use it whenever the user wants one of their own files, e.g. "find my resume", "find the Automate the
    Boring Stuff book", "the PDF about my project"."""
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
