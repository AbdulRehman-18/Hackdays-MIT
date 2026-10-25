"""Agent Skills (https://agentskills.io) with progressive disclosure.

At startup only each skill's name and description are loaded (a few tokens each). When the router picks a
skill, its full SKILL.md body is added to the agent's instructions; files it references are read on demand.
Skill instructions are guidance, never permissions: every action still goes through the policy gate."""
import re
from dataclasses import dataclass
from pathlib import Path

NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


@dataclass
class Skill:
    name: str
    description: str
    path: Path

    def body(self) -> str:
        text = (self.path / "SKILL.md").read_text()
        return re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.S).strip()


def parse_frontmatter(text: str) -> dict[str, str]:
    m = re.match(r"\A---\n(.*?)\n---\n", text, re.S)
    if not m:
        raise ValueError("SKILL.md must start with YAML frontmatter between --- lines")
    out, key = {}, None
    for line in m.group(1).splitlines():
        kv = re.match(r"^([A-Za-z][\w-]*):\s*(.*)$", line)
        if kv:
            key, val = kv.group(1), kv.group(2).strip()
            out[key] = val.strip("\"'") if val not in ("|", ">", ">-", "|-") else ""
        elif key and line.startswith((" ", "\t")) and key in out and isinstance(out[key], str):
            if not re.match(r"^\s+[\w-]+:", line) or key == "description":
                out[key] = (out[key] + " " + line.strip()).strip()
    return out


def validate(path: Path) -> list[str]:
    """Problems with a skill directory under the Agent Skills spec (empty means valid)."""
    problems = []
    f = path / "SKILL.md"
    if not f.exists():
        return ["missing SKILL.md"]
    try:
        fm = parse_frontmatter(f.read_text())
    except ValueError as e:
        return [str(e)]
    name, desc = fm.get("name", ""), fm.get("description", "")
    if not NAME.match(name) or len(name) > 64:
        problems.append(f"name {name!r} must be 1-64 lowercase letters, digits and single hyphens")
    if name != path.name:
        problems.append(f"name {name!r} must match the directory name {path.name!r}")
    if not desc or len(desc) > 1024:
        problems.append("description must be 1-1024 characters")
    if "compatibility" in fm and len(fm["compatibility"]) > 500:
        problems.append("compatibility must be at most 500 characters")
    return problems


def load(root: Path) -> list[Skill]:
    skills = []
    for d in sorted(p for p in root.glob("*") if p.is_dir()):
        if validate(d):
            continue
        fm = parse_frontmatter((d / "SKILL.md").read_text())
        skills.append(Skill(fm["name"], fm["description"], d))
    return skills


def menu(skills: list[Skill]) -> str:
    return "\n".join(f"- {s.name}: {s.description}" for s in skills)
