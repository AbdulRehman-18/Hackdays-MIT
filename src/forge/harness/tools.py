"""Tools: plain Python functions whose typed signature becomes the argument schema the model must follow.

    @tool(group="apps")
    def open_app(name: Annotated[str, Field(description="app name, e.g. Safari")]) -> str:
        "Open an application by name."
"""
import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, create_model


@dataclass
class Tool:
    name: str
    description: str
    args: type[BaseModel]
    fn: Callable[..., Any]
    group: str = "core"
    risk: str = "low"  # a hint for the policy gate; the gate makes the final call from context

    def args_schema(self) -> dict:
        return _clean(self.args.model_json_schema())

    def run(self, args: BaseModel) -> str:
        return self.run_full(args)[0]

    def run_full(self, args: BaseModel) -> tuple[str, dict]:
        """(text for the model, extras for the faces). A tool may return {"text": ..., "diff": ..., ...}."""
        out = self.fn(**args.model_dump())
        if isinstance(out, dict):
            return str(out.get("text", "ok")), {k: v for k, v in out.items() if k != "text"}
        return ("ok" if out is None else str(out)), {}


def tool(name: str | None = None, group: str = "core", risk: str = "low"):
    def wrap(fn):
        fields = {}
        for p in inspect.signature(fn).parameters.values():
            ann = p.annotation if p.annotation is not inspect.Parameter.empty else str
            fields[p.name] = (ann, ... if p.default is inspect.Parameter.empty else p.default)
        model = create_model(f"{fn.__name__}_args", __config__=ConfigDict(extra="forbid"), **fields)
        desc = inspect.getdoc(fn) or fn.__name__
        return Tool(name or fn.__name__, desc, model, fn, group, risk)
    return wrap


def _clean(schema, in_properties: bool = False):
    """Drop pydantic's titles: they cost tokens and some decoders choke on them. Inside "properties" the keys
    are argument names, so an argument that is itself called "title" (a note's title) must stay."""
    if isinstance(schema, dict):
        return {k: _clean(v, k == "properties" and not in_properties) for k, v in schema.items()
                if in_properties or k != "title"}
    if isinstance(schema, list):
        return [_clean(v) for v in schema]
    return schema
