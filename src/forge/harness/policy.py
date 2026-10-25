"""The policy gate: every action is rated from its real context, never from the model's own opinion of it.

allow   run it
confirm ask the user first; no answer counts as no
block   never run it"""
import re
from dataclasses import dataclass

from .schema import Action
from .tools import Tool

RISKY_WORDS = re.compile(r"\b(delete|erase|remove|trash|wipe|format|uninstall|send|post|publish|tweet|submit|pay|"
                         r"purchase|buy|order|checkout|transfer|sign ?out|log ?out|shut ?down|restart)\b", re.I)
DANGEROUS_TEXT = re.compile(r"rm\s+-[a-z]*[rf]|sudo\s|mkfs|dd\s+if=|:\(\)\s*\{|curl[^|]*\|\s*(ba|z)?sh|chmod\s+-R\s+777"
                            r"|diskutil\s+erase", re.I)
# a command that can only look: runs without asking (no redirection, chaining or substitution)
READ_ONLY = re.compile(r"^\s*(?:ls|pwd|cat|head|tail|wc|grep|rg|find|tree|file|stat|du|which|echo|date|whoami|uname"
                       r"|dir|type|where|gci|gc|Get-ChildItem|Get-Content|Get-Location|Select-String"
                       r"|git\s+(?:status|diff|log|show|branch|remote -v|rev-parse|ls-files|blame))\b[^;&|<>`$]*$",
                       re.I)
DESTRUCTIVE = re.compile(r"\brm\s+-[a-z]*r[a-z]*f?\s+(?:/|~|\$HOME|\*)(?:\s|$)|\bsudo\b|\bmkfs|\bdd\s+if=|:\(\)\s*\{"
                         r"|curl[^|]*\|\s*(?:ba|z)?sh|wget[^|]*\|\s*(?:ba|z)?sh|\bdiskutil\s+erase|chmod\s+-R\s+777\s+/"
                         r"|git\s+push\s+(?:-f|--force)|\bshutdown\b|\breboot\b", re.I)
CARD_NUMBER = re.compile(r"\b(?:\d[ -]?){13,19}\b")
RISKY_KEYS = [{"cmd", "q"}, {"cmd", "delete"}, {"cmd", "backspace"}, {"cmd", "shift", "delete"}, {"cmd", "w"},
              {"cmd", "option", "esc"}, {"alt", "f4"}]
MOD_ALIASES = {"command": "cmd", "meta": "cmd", "super": "cmd", "control": "ctrl", "opt": "option", "alt": "option",
               "return": "enter", "escape": "esc", "del": "delete"}


@dataclass
class Verdict:
    decision: str  # allow, confirm or block
    reason: str = ""


def keys(combo: str) -> set[str]:
    return {MOD_ALIASES.get(k, k) for k in re.split(r"[+\s]+", combo.lower().strip()) if k}


def goal_is_risky(goal: str) -> bool:
    return bool(RISKY_WORDS.search(goal))


class Policy:
    def check(self, action: Action, tool: Tool, context: dict | None = None) -> Verdict:
        """context may carry what the action will touch, e.g. {"element": "Send button", "app": "Mail"}."""
        ctx = context or {}
        args = action.args
        text = str(args.get("text", ""))
        if text and DANGEROUS_TEXT.search(text):
            return Verdict("block", "that text looks like a destructive command")
        if text and CARD_NUMBER.search(text):
            return Verdict("block", "I never type card or account numbers")
        if tool.risk == "high":
            return Verdict("confirm", tool.description.split(".")[0])
        if tool.risk == "edit":
            return Verdict("confirm", f"edit {args.get('path', 'a file')}")
        if tool.risk == "command":
            cmd = str(args.get("command", ""))
            if DESTRUCTIVE.search(cmd):
                return Verdict("block", "that command could destroy data or the system")
            if READ_ONLY.match(cmd):
                return Verdict("allow")
            return Verdict("confirm", f"run `{cmd[:120]}`")
        if action.tool == "key":
            k = keys(str(args.get("combo", "")))
            if any(r <= k for r in RISKY_KEYS):
                return Verdict("confirm", f"pressing {args.get('combo')} can close or delete things")
            if k == {"enter"} and RISKY_WORDS.search(str(ctx.get("focused", ""))):
                return Verdict("confirm", f"pressing Enter in “{ctx['focused']}”")
        target = str(ctx.get("element", ""))
        if action.tool in ("click_element", "click") and target and RISKY_WORDS.search(target):
            return Verdict("confirm", f"clicking “{target}”")
        if action.tool == "type_into" and args.get("press_enter") and RISKY_WORDS.search(target):
            return Verdict("confirm", f"submitting “{target}”")
        return Verdict("allow")
