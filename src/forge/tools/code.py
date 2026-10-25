"""Code mode: tools that work inside one project folder, like a coding agent in a terminal.

Every path is resolved inside the workspace and refused if it escapes it. Edits are exact string replacements
(the old text must appear exactly once), so a small model cannot rewrite a file it has not read; every change
comes back as a unified diff. Commands run in the workspace with a timeout; the policy gate asks before anything
that is not plainly read-only."""
import difflib
import fnmatch
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Annotated

from pydantic import Field

from ..harness.tools import Tool, tool

IGNORE = {".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache",
          "dist", "build", ".next", ".idea", ".DS_Store", "target", ".cache", ".tox"}
MAX_READ = 400  # lines per read_file call
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
ACTION = re.compile(r"\b(fix|add|implement|change|update|refactor|rename|remove|delete|create|write|make|build|"
                    r"improve|optimi[sz]e|convert|migrate|move|replace|upgrade|support)\b", re.I)


LOOKS = ("list_files", "search_code", "read_file")


def finish_check(steps: list[dict], request: str) -> str:
    """Code mode refuses to finish too early: a note for the model, or "" when it may respond.
    It may not answer about the project without reading it, stop while the last check it ran is failing, or stop
    after only describing a change it was asked to make."""
    if not any(s["tool"] in LOOKS for s in steps) and not ACTION.search(request):
        return ("Look at the project before you answer: read the README and the main source files from the file "
                "list with read_file (and search_code if needed). Do not answer from the file names alone.")
    edits = [i for i, s in enumerate(steps) if s["tool"] in ("edit_file", "write_file") and s.get("status") == "ok"]
    runs = [i for i, s in enumerate(steps) if s["tool"] == "run_command" and s.get("status") != "denied"]
    if runs and steps[runs[-1]].get("exit_code", 0) != 0 and (not edits or edits[-1] < runs[-1]):
        return ("The last command failed and nothing has been changed since. Fix the cause with edit_file now "
                "(read the file first if needed), then run the check again. Do not just describe the fix.")
    if ACTION.search(request) and not edits:
        return ("The user asked you to make a change, but no file has been edited yet. Make the change with "
                "edit_file or write_file now instead of describing it.")
    if edits and (not runs or runs[-1] < edits[-1]):
        return ("You changed files but have not checked the result since. Run the tests (or a quick command that "
                "uses the change) with run_command before you finish.")
    return ""


def branch(root: Path) -> str:
    try:
        r = subprocess.run(["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True,
                           text=True, timeout=3)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def inside(root: Path, path: str) -> Path:
    """The path resolved inside the workspace; ValueError if it points outside."""
    root = root.resolve()
    p = (root / os.path.expanduser(path or ".")).resolve()
    if p != root and root not in p.parents:
        raise ValueError(f"{path} is outside the project folder {root}")
    return p


def walk(root: Path, start: Path | None = None):
    for dirpath, dirs, files in os.walk(start or root):
        dirs[:] = sorted(d for d in dirs if d not in IGNORE and not d.startswith(".") or d in (".github",))
        for f in sorted(files):
            if f not in IGNORE:
                yield Path(dirpath) / f


def find(root: Path, q: str, limit: int = 50) -> list[str]:
    """Workspace files whose path contains q (for @-mentions)."""
    q = q.lower()
    out = []
    for p in walk(root):
        rel = str(p.relative_to(root))
        if q in rel.lower():
            out.append(rel)
            if len(out) >= limit:
                break
    return out


def unified(old: str, new: str, rel: str) -> tuple[str, int, int]:
    lines = list(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                      f"a/{rel}", f"b/{rel}", n=3))
    added = sum(1 for line in lines if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in lines if line.startswith("-") and not line.startswith("---"))
    return "".join(line if line.endswith("\n") else line + "\n" for line in lines), added, removed


def plan_edit(root: Path, path: str, old: str, new: str, replace_all: bool = False) -> tuple[Path, str, str]:
    """(file, current text, text after the edit). Raises ValueError with advice the model can act on."""
    p = inside(root, path)
    if not p.is_file():
        raise ValueError(f"{path} does not exist. To create a file use write_file.")
    text = p.read_text(errors="replace")
    if not old:
        raise ValueError("old_text is empty. Copy the exact lines to change from read_file (without line numbers).")
    n = text.count(old)
    if n == 0:
        near = difflib.get_close_matches(old.strip().splitlines()[0] if old.strip() else old,
                                         [ln.strip() for ln in text.splitlines()], n=2, cutoff=0.6)
        hint = f" The closest lines are: {near}." if near else ""
        raise ValueError(f"old_text was not found in {path}. Read the file again and copy the text exactly, "
                         f"including indentation.{hint}")
    if n > 1 and not replace_all:
        raise ValueError(f"old_text appears {n} times in {path}. Include more surrounding lines so it is unique, "
                         "or set replace_all.")
    new = _match_indent(text, old, new)
    return p, text, text.replace(old, new) if replace_all else text.replace(old, new, 1)


def _match_indent(text: str, old: str, new: str) -> str:
    """When old_text starts mid-line after indentation (the model copied "return x" without its 4 spaces), give
    the following lines of new_text that same indentation, so the edit keeps the code's structure."""
    at = text.find(old)
    lead = text[text.rfind("\n", 0, at) + 1:at]
    if not lead or lead.strip() or "\n" not in new:
        return new
    first, *rest = new.split("\n")
    if all(not line.strip() or line.startswith(lead) for line in rest):
        return new  # already indented
    return "\n".join([first, *[lead + line if line.strip() else line for line in rest]])


def preview(root: Path, tool_name: str, args: dict) -> str:
    """What an action will change, shown in the approval prompt: a diff, or the command."""
    root = root.resolve()
    try:
        if tool_name == "edit_file":
            p, before, after = plan_edit(root, args["path"], args["old_text"], args["new_text"],
                                         args.get("replace_all", False))
            return unified(before, after, str(p.relative_to(root)))[0]
        if tool_name == "write_file":
            p = inside(root, args["path"])
            before = p.read_text(errors="replace") if p.is_file() else ""
            return unified(before, args["content"], str(p.relative_to(root)))[0]
        if tool_name == "run_command":
            return f"$ {args['command']}"
    except (ValueError, KeyError, OSError) as e:
        return f"(cannot preview: {e})"
    return ""


def workspace_tools(root: Path) -> list[Tool]:
    """The Code-mode tools, bound to one project folder."""
    root = root.resolve()

    def rel(p: Path) -> str:
        return str(p.relative_to(root)) or "."

    @tool(group="code")
    def list_files(path: Annotated[str, Field(description="folder inside the project, '.' for the top")] = ".",
                   depth: Annotated[int, Field(description="how many folder levels to show, 1 to 4")] = 2) -> str:
        """List the project's files and folders as a tree (skips .git, node_modules, virtualenvs, caches)."""
        start = inside(root, path)
        if not start.is_dir():
            return f"error: {path} is not a folder"
        depth = max(1, min(4, depth))
        lines = []
        for dirpath, dirs, files in os.walk(start):
            level = len(Path(dirpath).relative_to(start).parts)
            dirs[:] = sorted(d for d in dirs if d not in IGNORE and not d.startswith(".")) if level < depth - 1 else []
            pad = "  " * level
            if level:
                lines.append(f"{pad[:-2]}{Path(dirpath).name}/")
            lines += [f"{pad}{f}" for f in sorted(files) if f not in IGNORE][:80]
            if len(lines) > 300:
                lines.append("… (more; list a subfolder)")
                break
        return f"{rel(start)}/\n" + "\n".join(lines or ["(empty)"])

    @tool(group="code")
    def search_code(pattern: Annotated[str, Field(description="text or regular expression to find")],
                    path: Annotated[str, Field(description="folder or file to search, '.' for everything")] = ".",
                    glob: Annotated[str, Field(description="only files like *.py, or empty")] = "") -> str:
        """Search the project's files for text (like ripgrep). Returns file:line: matching line."""
        start = inside(root, path)
        if shutil.which("rg"):
            cmd = ["rg", "-n", "--no-heading", "-S", "--max-count", "20", "--max-columns", "200"]
            cmd += ["-g", glob] if glob else []
            r = subprocess.run([*cmd, "--", pattern, str(start)], capture_output=True, text=True, timeout=20)
            hits = [line.replace(str(root) + "/", "", 1) for line in r.stdout.splitlines()]
        else:
            rx = re.compile(pattern, re.I)
            hits = []
            for p in [start] if start.is_file() else walk(root, start):
                if glob and not fnmatch.fnmatch(p.name, glob):
                    continue
                try:
                    for i, line in enumerate(p.read_text(errors="ignore").splitlines(), 1):
                        if rx.search(line):
                            hits.append(f"{rel(p)}:{i}:{line.strip()[:200]}")
                except OSError:
                    continue
        if not hits:
            return f"No matches for {pattern!r}."
        more = f"\n… {len(hits) - 120} more matches; narrow the search" if len(hits) > 120 else ""
        return "\n".join(hits[:120]) + more

    @tool(group="code")
    def read_file(path: Annotated[str, Field(description="file path inside the project")],
                  start: Annotated[int, Field(description="first line to show (1-based)")] = 1,
                  limit: Annotated[int, Field(description=f"how many lines, at most {MAX_READ}")] = MAX_READ) -> str:
        """Read a file with line numbers. Read before you edit: edit_file needs the exact text."""
        p = inside(root, path)
        if not p.is_file():
            return f"error: {path} does not exist"
        lines = p.read_text(errors="replace").splitlines()
        start, limit = max(1, start), max(1, min(MAX_READ, limit))
        part = lines[start - 1:start - 1 + limit]
        body = "\n".join(f"{i:>5}\t{line}" for i, line in enumerate(part, start))
        tail = f"\n… lines {start + len(part)}-{len(lines)} not shown (read with start=)" \
            if start - 1 + len(part) < len(lines) else ""
        return {"text": f"{rel(p)} ({len(lines)} lines)\n{body}{tail}", "path": rel(p)}

    @tool(group="code", risk="edit")
    def edit_file(path: Annotated[str, Field(description="file path inside the project")],
                  old_text: Annotated[str, Field(description="exact text to replace, copied from read_file "
                                                             "without the line numbers; must be unique")],
                  new_text: Annotated[str, Field(description="the replacement text")],
                  replace_all: Annotated[bool, Field(description="replace every occurrence")] = False) -> str:
        """Change part of a file by replacing exact text. Returns the diff."""
        p, before, after = plan_edit(root, path, old_text, new_text, replace_all)
        if before == after:
            return {"text": f"No change: old_text and new_text are the same in {rel(p)}.", "path": rel(p),
                    "noop": True}
        p.write_text(after)
        diff, a, d = unified(before, after, rel(p))
        return {"text": f"Edited {rel(p)} (+{a} -{d})\n{diff[:3000]}", "diff": diff, "path": rel(p)}

    @tool(group="code", risk="edit")
    def write_file(path: Annotated[str, Field(description="file path inside the project")],
                   content: Annotated[str, Field(description="the whole new file content")]) -> str:
        """Create a new file, or replace a whole file. For changes to an existing file prefer edit_file."""
        p = inside(root, path)
        before = p.read_text(errors="replace") if p.is_file() else ""
        p.parent.mkdir(parents=True, exist_ok=True)
        if before == content:
            return {"text": f"No change: {rel(p)} already has exactly this content.", "path": rel(p), "noop": True}
        p.write_text(content)
        diff, a, d = unified(before, content, rel(p))
        verb = "Updated" if before else "Created"
        return {"text": f"{verb} {rel(p)} (+{a} -{d})", "diff": diff, "path": rel(p)}

    @tool(group="code", risk="command")
    def run_command(command: Annotated[str, Field(description="shell command, run in the project folder")],
                    timeout: Annotated[int, Field(description="seconds before it stops, max 600")] = 120) -> str:
        """Run a terminal command in the project (tests, builds, git, scripts). Returns its output and exit code."""
        try:
            r = subprocess.run(["/bin/zsh", "-lc", command], cwd=root, capture_output=True, text=True,
                               timeout=max(1, min(600, timeout)), stdin=subprocess.DEVNULL,
                               env={**os.environ, "NO_COLOR": "1", "TERM": "dumb", "PY_COLORS": "0", "CI": "1"})
            out, code = ANSI.sub("", r.stdout + ("\n" + r.stderr if r.stderr else "")).strip(), r.returncode
        except subprocess.TimeoutExpired as e:
            out, code = f"{(e.stdout or '')}\n(stopped after {timeout} s)".strip(), 124
        if len(out) > 6000:
            out = "… (earlier output cut)\n" + out[-6000:]
        return {"text": f"$ {command}\nexit code {code}\n{out or '(no output)'}", "exit_code": code,
                "command": command, "output": out}

    return [list_files, search_code, read_file, edit_file, write_file, run_command]


def _works(root: Path, command: str) -> bool:
    """Whether a command succeeds in the same login shell run_command uses."""
    try:
        return subprocess.run(["/bin/zsh", "-lc", command], cwd=root, capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def test_command(root: Path) -> str:
    """The project's usual test command, guessed from its files."""
    if (root / "package.json").is_file():
        try:
            import json
            if "test" in json.loads((root / "package.json").read_text()).get("scripts", {}):
                return "npm test"
        except (ValueError, OSError):
            pass
    if (root / "Cargo.toml").is_file():
        return "cargo test"
    if (root / "go.mod").is_file():
        return "go test ./..."
    has_py_tests = any(p.suffix == ".py" and (p.name.startswith("test_") or p.name.endswith("_test.py"))
                       for p in walk(root))
    if has_py_tests or (root / "pytest.ini").is_file() or (root / "pyproject.toml").is_file():
        if (root / "uv.lock").is_file():
            return "uv run pytest -q"
        for venv in (".venv", "venv"):
            if (root / venv / "bin" / "pytest").exists():
                return f"{venv}/bin/pytest -q"
        if _works(root, "python3 -c 'import pytest'"):
            return "python3 -m pytest -q"
        return "uvx pytest -q" if shutil.which("uvx") else "python3 -m pytest -q"
    if (root / "Makefile").is_file():
        return "make test"
    return ""


def orientation(root: Path) -> str:
    """What a coding agent should know before its first step: layout, git state and how to test."""
    files = [str(p.relative_to(root)) for _, p in zip(range(120), walk(root), strict=False)]
    try:
        st = subprocess.run(["git", "-C", str(root), "status", "--short"], capture_output=True, text=True,
                            timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        st = ""
    test = test_command(root)
    readme = next((p for p in (root / "README.md", root / "readme.md", root / "README") if p.is_file()), None)
    intro = ""
    if readme:
        lines = readme.read_text(errors="replace").splitlines()[:30]
        intro = f"\nStart of {readme.name}:\n" + "\n".join(f"  | {ln}" for ln in lines)
    return (f"Project files ({len(files)}{'+' if len(files) == 120 else ''}):\n" + "\n".join(f"  {f}" for f in files)
            + (f"\nUncommitted changes:\n{st}" if st else "\nGit: no uncommitted changes." if (root / ".git").exists()
               else "")
            + (f"\nRun the tests with: {test}" if test else "") + intro)


GUIDE = """You are working in the project folder {root}{branch}. Work like a careful senior engineer:
- Explore before changing anything: list_files, search_code, and read_file the parts that matter.
- Change code with edit_file: old_text must be copied exactly from read_file (without the line numbers) and be
  unique. Use write_file only for new files. Never guess what a file contains: read it first.
- After a change, check it: run the tests or a quick command with run_command, read the output, fix failures.
- Keep going, one step at a time, until the whole task is done. Then choose respond.
- Use paths relative to the project folder. Do not create files the task does not need.
- If something is ambiguous or impossible, choose respond and explain what you need.
{orientation}"""
