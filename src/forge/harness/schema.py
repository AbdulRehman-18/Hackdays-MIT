"""The tool-call protocol Forge owns, instead of trusting each provider's function calling.

Every step the model must answer with one JSON object: {"thought": ..., "tool": ..., "args": {...}}.
`step_schema` turns the tools on offer into a schema the decoder can enforce, and `parse_action`
validates whatever came back and explains precisely what is wrong, so one repair turn usually fixes it."""
import difflib
import json
import re
from dataclasses import dataclass, field

from pydantic import ValidationError

from .tools import Tool


@dataclass
class Action:
    tool: str
    args: dict = field(default_factory=dict)
    thought: str = ""


class ActionError(ValueError):
    pass


def step_schema(tools: list[Tool]) -> dict:
    """One branch per tool, so a constrained decoder can only emit a known tool with its own arguments."""
    return {"anyOf": [{
        "type": "object",
        "properties": {
            "thought": {"type": "string", "description": "one short sentence: why this step"},
            "tool": {"type": "string", "enum": [t.name]},
            "args": t.args_schema(),
        },
        "required": ["thought", "tool", "args"],
        "additionalProperties": False,
    } for t in tools]}


def catalog(tools: list[Tool]) -> str:
    """The tools as compact text for the prompt (needed when the decoder cannot enforce the schema)."""
    lines = []
    for t in tools:
        props = t.args_schema().get("properties", {})
        req = set(t.args_schema().get("required", []))
        args = ", ".join(f"{k}: {v.get('type', 'any')}{'' if k in req else '?'}" for k, v in props.items())
        lines.append(f"- {t.name}({args}): {t.description}")
    return "\n".join(lines)


def _extract_json(text: str) -> object:
    text = re.sub(r"```(?:json)?", "", text.strip())
    start = text.find("{")
    if start < 0:
        raise ActionError("No JSON object found. Reply with exactly one JSON object.")
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError as e:
        raise ActionError(f"The JSON is malformed ({e.msg} at character {e.pos}).") from e
    return obj


def parse_action(text: str, tools: list[Tool]) -> Action:
    """Validate a model reply into an Action, or raise ActionError with a message written for the model."""
    obj = _extract_json(text)
    if not isinstance(obj, dict):
        raise ActionError("The reply must be a JSON object, not a list or a value.")
    name = obj.get("tool") or obj.get("name") or obj.get("function")
    args = obj.get("args", obj.get("arguments", obj.get("parameters", {})))
    by_name = {t.name: t for t in tools}
    if not isinstance(name, str) or not name:
        raise ActionError('Missing "tool". Pick one of: ' + ", ".join(by_name))
    if name not in by_name:
        close = difflib.get_close_matches(name, by_name, n=1)
        hint = f' Did you mean "{close[0]}"?' if close else ""
        raise ActionError(f'Unknown tool "{name}".{hint} Allowed tools: ' + ", ".join(by_name))
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ActionError(f'"args" for {name} must be a JSON object.')
    try:
        valid = by_name[name].args.model_validate(args)
    except ValidationError as e:
        problems = "; ".join(f"{'.'.join(map(str, err['loc'])) or 'args'}: {err['msg']}" for err in e.errors())
        raise ActionError(f"Bad arguments for {name}: {problems}.") from e
    return Action(name, valid.model_dump(), str(obj.get("thought", "")))


def repair_message(error: ActionError) -> str:
    return (f"Your last reply could not be used: {error} "
            "Reply again with exactly one JSON object: {\"thought\": ..., \"tool\": ..., \"args\": {...}}.")
