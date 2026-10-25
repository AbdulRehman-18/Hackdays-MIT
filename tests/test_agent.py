import json
from typing import Annotated

from pydantic import Field

from forge.harness import Agent, tool
from forge.harness.backends import Completion


class Scripted:
    """A fake backend that replies with a fixed list of texts and records what it was sent."""
    name, model = "fake", "scripted"

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def complete(self, messages, schema=None, temperature=0.2):
        self.calls.append({"messages": messages, "schema": schema})
        return Completion(self.replies.pop(0), 0.01, 10, 5, schema is not None)


def j(tool, **args):
    return json.dumps({"thought": "x", "tool": tool, "args": args})


opened = []


@tool(group="apps")
def open_app(name: Annotated[str, Field(description="app")]) -> str:
    """Open an app."""
    opened.append(name)
    return f"{name} is open"


def setup_function():
    opened.clear()


def test_runs_tool_then_finishes():
    b = Scripted(j("open_app", name="Notes"), j("done", summary="Notes is open."))
    r = Agent(b, [open_app]).run("open notes")
    assert r.finished and r.summary == "Notes is open."
    assert opened == ["Notes"]
    assert "Notes is open" in b.calls[1]["messages"][-1].content  # the result reaches the next prompt
    assert b.calls[0]["schema"]["anyOf"]  # constrained decoding was requested


def test_repairs_an_invalid_reply_once():
    b = Scripted("open the notes app please", j("open_app", name="Notes"), j("done", summary="ok"))
    r = Agent(b, [open_app]).run("open notes")
    assert r.finished and opened == ["Notes"]
    assert r.steps[0].attempts == 2 and "No JSON" in r.steps[0].first_try_error
    assert "could not be used" in b.calls[1]["messages"][-1].content


def test_gives_up_when_repair_also_fails():
    b = Scripted("nope", "still nope")
    r = Agent(b, [open_app]).run("open notes")
    assert not r.finished and "valid action" in r.summary and opened == []


def test_raw_mode_has_no_schema_and_no_repair():
    b = Scripted("nope")
    r = Agent(b, [open_app], constrained=False, repair=False).run("open notes")
    assert not r.finished and len(b.calls) == 1 and b.calls[0]["schema"] is None


def test_stops_going_in_circles():
    b = Scripted(*[j("open_app", name="Notes")] * 5)
    r = Agent(b, [open_app]).run("open notes")
    assert not r.finished and "circles" in r.summary and len(opened) == 2


def test_step_cap():
    b = Scripted(*[j("open_app", name=f"App{i}") for i in range(3)])
    r = Agent(b, [open_app], max_steps=3).run("open apps")
    assert not r.finished and "too many steps" in r.summary and len(opened) == 3


def test_tool_errors_are_reported_not_raised():
    @tool()
    def boom() -> str:
        """Always fails."""
        raise RuntimeError("kaput")
    b = Scripted(j("boom"), j("done", summary="failed"))
    r = Agent(b, [boom]).run("x")
    assert r.steps[0].result == "error: kaput"


def test_dry_run_executes_nothing(tmp_path):
    b = Scripted(j("open_app", name="Notes"), j("done", summary="ok"))
    r = Agent(b, [open_app], dry_run=True, trace_dir=str(tmp_path)).run("open notes")
    assert r.finished and opened == []
    kinds = [json.loads(line)["kind"] for line in next(tmp_path.iterdir()).read_text().splitlines()]
    assert kinds[0] == "start" and kinds[-1] == "end" and "action" in kinds
