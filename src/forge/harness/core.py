"""The one decision step every Forge loop uses (the chat agent and the screen agent alike).

    prompt ─► model, decoded against the step schema (one anyOf branch per tool)
           ─► validate ─► invalid? one repair turn that names the exact error
                         (wrong arguments: decoded against only that tool's schema, so it can only fix them)

and the one policy gate every action passes before it runs."""
from collections.abc import Callable
from dataclasses import dataclass

from .backends import Backend, Completion, Message
from .policy import Policy, Verdict
from .schema import Action, ActionError, parse_action, repair_message, step_schema
from .tools import Tool

Events = Callable[[str, dict], None]


@dataclass
class Decision:
    action: Action
    attempts: int  # 1: valid first time; 2: needed the repair turn
    first_error: str = ""


def _tool_of(error: ActionError, tools: list[Tool]) -> list[Tool]:
    """The tool a "Bad arguments for X" error is about, so the repair decodes against that branch only."""
    msg = str(error)
    if msg.startswith("Bad arguments for "):
        name = msg[len("Bad arguments for "):].split(":", 1)[0]
        return [t for t in tools if t.name == name] or tools
    return tools


def decide(backend: Backend, msgs: list[Message], tools: list[Tool], on_event: Events, constrained: bool = True,
           repair: bool = True, temperature: float = 0.2, purpose: str = "decide") -> Decision:
    """One validated action. Raises ActionError when even the repair turn is unusable."""
    def call(m: list[Message], offered: list[Tool], temp: float) -> Completion:
        c = backend.complete(m, schema=step_schema(offered) if constrained else None, temperature=temp)
        on_event("model", {"model": backend.model, "seconds": round(c.seconds, 3), "tokens": c.output_tokens,
                           "prompt_tokens": c.prompt_tokens, "constrained": c.constrained, "purpose": purpose,
                           "text": c.text[:500]})
        return c

    c = call(msgs, tools, temperature)
    try:
        return Decision(parse_action(c.text, tools), 1)
    except ActionError as e:
        if not repair:
            raise
        on_event("repair", {"error": str(e)})
        fixed = call(msgs + [Message("assistant", c.text), Message("user", repair_message(e))], _tool_of(e, tools),
                     max(0.0, temperature - 0.1))
        return Decision(parse_action(fixed.text, tools), 2, str(e))


def gate(policy: Policy, action: Action, tool_: Tool, context: dict, on_event: Events,
         approve: Callable[[Action, Verdict], bool] | None) -> tuple[str, str]:
    """("allow" | "blocked" | "denied", message). Context is what the action will really touch."""
    v = policy.check(action, tool_, context)
    on_event("policy", {"tool": action.tool, "decision": v.decision, "reason": v.reason})
    if v.decision == "block":
        return "blocked", f"I won't do that: {v.reason}."
    if v.decision == "confirm":
        ok = bool(approve and approve(action, v))
        on_event("approval", {"tool": action.tool, "allowed": ok, "reason": v.reason})
        if not ok:
            return "denied", "Okay, I didn't do it."
    return "allow", ""
