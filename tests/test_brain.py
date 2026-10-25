import json

import pytest

from forge.assistant import brain, hud_native
from forge.harness import core, tool
from forge.harness.backends import Completion
from forge.tools import apple


class Stub:
    def __init__(self, name, calls):
        self.name, self.calls = name, calls

    def fn(self, *a, **k):
        self.calls.append((self.name, a))
        return f"<{self.name}>"


@pytest.fixture
def router(monkeypatch):
    """A Router whose every side effect is recorded instead of run."""
    calls = []
    for mod, names in ((brain.web, ["weather", "youtube", "open_url"]),
                       (brain.system, ["battery", "volume", "media", "take_screenshot", "lock_screen", "read_screen"]),
                       (brain.senses, ["look", "sketch"]), (brain.files, ["open_file"]),
                       (brain.apple, ["calendar", "set_timer"])):
        for n in names:
            monkeypatch.setattr(mod, n, Stub(n, calls))
    monkeypatch.setattr(brain, "open_app", Stub("open_app", calls))
    monkeypatch.setattr(brain.system, "change_volume", lambda d: f"<change {d}>")
    return brain.Router(lambda s: calls.append(("say", s)), lambda m: True), calls


@pytest.mark.parametrize("text,path,first", [
    ("Hey Forge, what's the time", "instant", None),
    ("weather in London", "instant", ("weather", ("London",))),
    ("set a timer for 10 minutes", "instant", ("set_timer", (600,))),
    ("volume 40", "instant", ("volume", (40,))),
    ("what's on my calendar tomorrow", "instant", ("calendar", (1,))),
    ("play believer on youtube", "router", ("say", "Playing believer.")),
    ("open youtube and play lofi music", "router", ("say", "Playing lofi music.")),
    ("open Safari", "router", ("open_app", ("Safari",))),
    ("what am I holding?", "router", ("say", "Let me look.")),
    ("open agent dot py", "router", ("open_file", ("agent.py",))),
])
def test_monday_routes(router, text, path, first):
    r, calls = router
    got = r.route(text)
    assert got is not None and got[0] == path
    if first:
        assert calls[0] == first


@pytest.mark.parametrize("text", ["check the weather in London and save it as a note",
                                  "play believer on youtube and set the volume to 40",
                                  "open WhatsApp and send hi to Ali", "tell me a joke"])
def test_compound_and_open_requests_go_to_the_harness(router, text):
    r, calls = router
    assert r.route(text) is None and calls == []


def test_island_mode_by_voice(router):
    r, _ = router
    assert r.route("island mode") == ("instant", "Island mode! I'll live in the notch.")
    assert r.route("full screen mode") == ("instant", "Full screen mode!")


def test_timer_parsing_and_wording():
    assert apple.parse_duration("an hour and a half") == 5400
    assert apple.parse_duration("a timer for 1 hour 30 minutes") == 5400
    assert apple.say_duration(90) == "1 minute 30 seconds"
    assert apple.cancel_timers() == "There's no timer running."


@tool(group="t")
def add_note(title: str, text: str) -> str:
    """Save a note."""
    return "ok"


class Scripted:
    name, model = "fake", "scripted"

    def __init__(self, *replies):
        self.replies, self.schemas = list(replies), []

    def complete(self, messages, schema=None, temperature=0.2):
        self.schemas.append(schema)
        return Completion(self.replies.pop(0), 0.01, 1, 1, schema is not None)


def test_repair_for_bad_arguments_decodes_against_that_tool_only():
    @tool(group="t")
    def other(x: int) -> str:
        """Other."""
        return "ok"
    b = Scripted(json.dumps({"thought": "", "tool": "add_note", "args": {"text": "hi"}}),
                 json.dumps({"thought": "", "tool": "add_note", "args": {"title": "Hi", "text": "hi"}}))
    d = core.decide(b, [], [add_note, other], lambda k, v: None)
    assert d.attempts == 2 and d.action.args == {"title": "Hi", "text": "hi"}
    assert len(b.schemas[0]["anyOf"]) == 2 and len(b.schemas[1]["anyOf"]) == 1  # narrowed to add_note


def test_hud_receives_monday_calls():
    hud_native._js.clear()
    hud_native._st.update(talking=False, agent=False)
    for msg in [{"type": "state", "state": "thinking"}, {"type": "caption", "who": "you", "text": "hi"},
                {"type": "caption", "who": "assistant", "text": "Hello!"}, {"type": "wake"},
                {"type": "cursor", "x": 10, "y": 20, "ms": 300}, {"type": "agent_cursor", "on": False},
                {"type": "agent_cursor", "on": True}, {"type": "state", "state": "waiting"},
                {"type": "prefs", "prefs": {"hud_mode": "island"}}]:
        hud_native.on_event(msg)
    js = hud_native._js
    assert js[0] == 'H.state("thinking")' and js[1] == 'H.say("you","hi")' and js[2] == 'H.say("forge","Hello!")'
    assert "H.wake()" in js and "H.move(10.0,20.0,300)" in js and "H.cursor(true)" in js
    assert js.count('H.state("thinking")') == 2  # "waiting" is shown as thinking
    assert js[-1] == 'H.mode("island")'
