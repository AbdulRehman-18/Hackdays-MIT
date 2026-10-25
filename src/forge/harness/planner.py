"""Planning: turn a request into the ordered sub-tasks it really contains, before acting.

Small models decide greedily. Given "check the weather and save it as a note" they fetch the weather, see a
result, and answer, forgetting the second half. One constrained call up front turns the request into a
checklist; the loop then works item by item and is not allowed to answer while an item is still open.

Planning only runs when a request looks compound (and, then, also, commas...), so simple requests cost
nothing extra."""
import re
from dataclasses import dataclass

from .backends import Backend, Message
from .schema import _extract_json
from .tools import Tool

MAX_ITEMS = 5
COMPOUND = re.compile(r"\b(and|then|also|after that|afterwards|plus|as well|too|both|next)\b|[,;]", re.I)

PLAN = """You plan for {name}, an assistant that works on the user's Mac with the tools below.
Split the user's request into the separate things it asks for, in the order they should happen.
Rules:
- One item per thing the user wants done or answered. Most requests have 1 to 3 items; never more than {max}.
- For each item pick the one tool that does it. Use "respond" for an item that is only writing, explaining,
  summarising or answering from what earlier items found.
- Anything done inside an app or a website by clicking and typing (sending a message, filling in a form,
  using an app's own buttons) is ONE computer_use item, including opening that app.
- Prefer a direct tool over computer_use whenever one does the whole item (youtube, add_note, set_timer...).
- Research ("research", "compare", "find out about") is a search item, then a read_webpage item, then respond.
- Only plan what the user asked for. Do not add checks, confirmations or extra steps.
- Write each task as a short instruction that includes the details it needs (names, places, times, text).
Reply with JSON only: {{"items": [{{"task": "...", "tool": "..."}}]}}
Tools:
{tools}"""

STEP_PLAN = """You plan for an agent that operates a Mac one action at a time (open apps and pages, click, type,
press keys). Write the short list of steps that reaches the goal, in order, at most {max} steps.
Each step is one short instruction, e.g. "Open WhatsApp", "Search for the chat with Ali", "Type hi and press Enter".
Only the steps the goal needs. Reply with JSON only: {{"steps": ["...", "..."]}}"""


@dataclass
class PlanItem:
    task: str
    tool: str
    status: str = "todo"  # todo, done or skipped
    nudged: bool = False  # the loop already pushed back once on answering before this item was done

    @property
    def open(self) -> bool:
        return self.status == "todo" and self.tool != "respond"


def needs_plan(text: str) -> bool:
    """Plan only requests that look like more than one thing; a plain question goes straight to the loop."""
    return len(text.split()) >= 5 and bool(COMPOUND.search(text))


def plan_schema(tools: list[Tool]) -> dict:
    names = sorted({t.name for t in tools if t.name not in ("respond", "use_skill")} | {"respond"})
    return {"type": "object", "properties": {"items": {
        "type": "array", "minItems": 1, "maxItems": MAX_ITEMS,
        "items": {"type": "object", "properties": {"task": {"type": "string"}, "tool": {"type": "string",
                                                                                        "enum": names}},
                  "required": ["task", "tool"], "additionalProperties": False}}},
        "required": ["items"], "additionalProperties": False}


def make_plan(backend: Backend, request: str, tools: list[Tool], catalog: str, name: str = "Forge",
              context: str = "") -> list[PlanItem]:
    """The request as a checklist. Returns [] when planning fails, so the caller just runs unplanned."""
    names = {t.name for t in tools} | {"respond"}
    msgs = [Message("system", PLAN.format(name=name, max=MAX_ITEMS, tools=catalog)),
            Message("user", (f"Conversation so far:\n{context}\n\n" if context else "") + f"Request: {request}")]
    try:
        obj = _extract_json(backend.complete(msgs, schema=plan_schema(tools), temperature=0.1).text)
    except Exception:
        return []
    items = []
    for it in (obj.get("items") or [])[:MAX_ITEMS] if isinstance(obj, dict) else []:
        if isinstance(it, dict) and str(it.get("task", "")).strip() and it.get("tool") in names:
            items.append(PlanItem(str(it["task"]).strip(), str(it["tool"])))
    return items


def make_steps(backend: Backend, goal: str) -> list[str]:
    """A short step list for the screen agent; [] when it fails or the goal is a single step."""
    schema = {"type": "object", "properties": {"steps": {"type": "array", "maxItems": 8, "items": {"type": "string"}}},
              "required": ["steps"], "additionalProperties": False}
    msgs = [Message("system", STEP_PLAN.format(max=8)), Message("user", f"Goal: {goal}")]
    try:
        obj = _extract_json(backend.complete(msgs, schema=schema, temperature=0.1).text)
    except Exception:
        return []
    steps = [str(s).strip() for s in (obj.get("steps") or []) if str(s).strip()] if isinstance(obj, dict) else []
    return steps[:8] if len(steps) > 1 else []


def checklist(items: list[PlanItem], instruct: bool = True) -> str:
    """The plan as the model sees it each step: what is done, what to do now, what comes after."""
    now = next((i for i, it in enumerate(items) if it.open), None)
    lines = ["[Plan for this request]"]
    for i, it in enumerate(items):
        mark = "now" if i == now else ("done" if it.status == "done" else it.status)
        lines.append(f"{i + 1}. [{mark}] {it.task} (tool: {it.tool})")
    if now is not None and instruct:
        lines.append(f"Work on item {now + 1} now. Choose respond only when every item is done.")
    return "\n".join(lines)


def advance(items: list[PlanItem], tool_name: str) -> PlanItem | None:
    """Mark the first open item done by this tool. Helper calls (find_files before read_file) match nothing."""
    for it in items:
        if it.open and it.tool == tool_name:
            it.status = "done"
            return it
    return None


def as_dicts(items: list[PlanItem]) -> list[dict]:
    return [{"task": it.task, "tool": it.tool, "status": it.status} for it in items]
