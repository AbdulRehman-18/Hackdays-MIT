import json
from typing import Annotated

from pydantic import Field

from forge.harness import Agent, tool
from forge.harness.backends import Completion, Delta
from forge.harness.chat import ChatAgent, Turn
from forge.harness.planner import PlanItem, advance, checklist, needs_plan


class Scripted:
    """Replies with fixed texts to complete() and streams a fixed answer; records every prompt."""
    name, model = "fake", "scripted"

    def __init__(self, *replies, answer="ok"):
        self.replies, self.calls, self.answer = list(replies), [], answer

    def complete(self, messages, schema=None, temperature=0.2):
        self.calls.append({"messages": messages, "schema": schema})
        return Completion(self.replies.pop(0), 0.01, 10, 5, schema is not None)

    def stream(self, messages, think=False, temperature=0.6):
        self.calls.append({"messages": messages, "schema": None, "stream": True})
        if self.answer:
            yield Delta("text", self.answer)
        yield Delta("done")


def j(tool_, **args):
    return json.dumps({"thought": "x", "tool": tool_, "args": args})


def plan(*items):
    return json.dumps({"items": [{"task": t, "tool": k} for t, k in items]})


ran = []


@tool(group="t")
def weather(place: Annotated[str, Field(description="city")]) -> str:
    """Current weather."""
    ran.append("weather")
    return f"{place}: 14C, rain"


@tool(group="t")
def add_note(title: Annotated[str, Field(description="title")], text: Annotated[str, Field(description="body")]) -> str:
    """Save a note."""
    ran.append("add_note")
    return f"Saved the note {title!r}."


def setup_function():
    ran.clear()


def test_needs_plan_only_for_compound_requests():
    assert needs_plan("check the weather in London and save it as a note")
    assert needs_plan("open WhatsApp, then send hi to Ali")
    assert not needs_plan("what's the weather in London")
    assert not needs_plan("tell me a joke")


def test_title_arguments_survive_schema_cleaning():
    s = add_note.args_schema()
    assert set(s["required"]) <= set(s["properties"])  # "title" used to be stripped as if it were metadata
    assert "title" not in s["properties"]["title"]


def test_plan_keeps_going_until_every_item_is_done():
    # the model tries to answer after the weather; the open plan item pushes it back to add_note
    b = Scripted(plan(("Get the London weather", "weather"), ("Save it as a note", "add_note")),
                 j("weather", place="London"), j("respond"),
                 j("add_note", title="London", text="14C, rain"), j("respond"), answer="Done.")
    r = ChatAgent(b, [weather, add_note]).run([], Turn("user", "check the weather in London and save it as a note"))
    assert ran == ["weather", "add_note"]
    assert r.text == "Done."
    nudge = b.calls[3]["messages"][-1].content
    assert "Not finished yet" in nudge and "Save it as a note" in nudge


def test_item_that_cannot_be_done_is_skipped_after_one_push_back():
    b = Scripted(plan(("Get the weather", "weather"), ("Save it as a note", "add_note")),
                 j("weather", place="Paris"), j("respond"), j("respond"), j("respond"))
    agent = ChatAgent(b, [weather, add_note])
    agent.run([], Turn("user", "get the weather in Paris and save it as a note"))
    assert ran == ["weather"]
    assert [it.status for it in agent.plan] == ["done", "skipped"]
    answer_prompt = b.calls[-1]["messages"][-1].content
    assert "[skipped] Save it as a note" in answer_prompt and "Work on item" not in answer_prompt


def test_invalid_step_mid_plan_is_recorded_not_claimed():
    b = Scripted(plan(("Get the weather", "weather"), ("Save it as a note", "add_note")),
                 j("weather", place="Rome"), j("add_note", text="no title"), j("add_note", text="still none"),
                 j("respond"))
    r = ChatAgent(b, [weather, add_note]).run([], Turn("user", "get the weather in Rome and then save it as a note"))
    assert ran == ["weather"]
    assert r.steps[-1]["status"] == "error" and r.steps[-1]["tool"] == "add_note"


def test_simple_request_makes_no_plan_call():
    b = Scripted(j("weather", place="Oslo"), j("respond"))
    ChatAgent(b, [weather, add_note]).run([], Turn("user", "weather in Oslo"))
    assert all("items" not in json.dumps(c["schema"] or {}) for c in b.calls)


def test_empty_streamed_answer_is_retried():
    b = Scripted("Here you go.", answer="")  # "hi" needs no tool: answered directly, no decision call
    r = ChatAgent(b, [weather]).run([], Turn("user", "hi"))
    assert r.text == "Here you go."


def test_plain_questions_skip_the_decision_and_tool_requests_do_not():
    b = Scripted(answer="391")
    ChatAgent(b, [weather]).run([], Turn("user", "what is 17 times 23"))
    assert all(c.get("stream") for c in b.calls)  # one streamed call, nothing else
    b = Scripted(j("weather", place="Oslo"), j("respond"), answer="cold")
    ChatAgent(b, [weather]).run([], Turn("user", "weather in Oslo"))
    assert ran == ["weather"]


def test_decide_and_answer_share_one_cacheable_system_prompt():
    b = Scripted(j("weather", place="Oslo"), j("respond"), answer="cold")
    ChatAgent(b, [weather]).run([], Turn("user", "weather in Oslo"))
    systems = {c["messages"][0].content for c in b.calls}
    assert len(systems) == 1 and "[" not in next(iter(systems)).split("Tools:")[0][-40:]  # no clock in it


def test_checklist_marks_the_current_item():
    items = [PlanItem("a", "weather", "done"), PlanItem("b", "add_note"), PlanItem("c", "respond")]
    text = checklist(items)
    assert "1. [done] a" in text and "2. [now] b" in text and "Work on item 2" in text
    assert advance(items, "weather") is None and advance(items, "add_note") is items[1]


def test_screen_agent_shows_its_step_plan():
    b = Scripted(json.dumps({"steps": ["Open WhatsApp", "Find Ali", "Type hi and press Enter"]}),
                 j("done", summary="sent"))
    r = Agent(b, [weather], plan=True).run("open WhatsApp and send hi to Ali")
    assert r.finished
    assert "2. Find Ali" in b.calls[1]["messages"][-1].content
