import pytest

from forge.harness.agent import done
from forge.harness.schema import ActionError, parse_action, step_schema
from forge.tools.apps import open_app, open_url

TOOLS = [open_app, open_url, done]


def test_schema_has_one_branch_per_tool_with_its_own_args():
    s = step_schema(TOOLS)
    branches = {b["properties"]["tool"]["enum"][0]: b for b in s["anyOf"]}
    assert set(branches) == {"open_app", "open_url", "done"}
    assert branches["open_app"]["properties"]["args"]["required"] == ["name"]
    assert branches["open_app"]["additionalProperties"] is False
    assert "title" not in str(s)


def test_parses_clean_json():
    a = parse_action('{"thought": "t", "tool": "open_app", "args": {"name": "Notes"}}', TOOLS)
    assert (a.tool, a.args, a.thought) == ("open_app", {"name": "Notes"}, "t")


def test_parses_json_wrapped_in_prose_and_fences():
    a = parse_action('Sure!\n```json\n{"tool": "open_url", "args": {"url": "x.com"}}\n```', TOOLS)
    assert a.tool == "open_url"


def test_accepts_common_alias_keys():
    a = parse_action('{"name": "done", "arguments": {"summary": "hi"}}', TOOLS)
    assert a.args == {"summary": "hi"}


@pytest.mark.parametrize("text, msg", [
    ("I will open Notes.", "No JSON object"),
    ('{"tool": "open_app", "args": {"name": "x"', "malformed"),
    ('{"tool": "open_ap", "args": {"name": "x"}}', 'Did you mean "open_app"'),
    ('{"tool": "open_app", "args": {}}', "name: Field required"),
    ('{"tool": "open_app", "args": {"name": "x", "extra": 1}}', "extra"),
    ('{"args": {}}', 'Missing "tool"'),
    ('[1, 2]', "JSON object"),
])
def test_errors_tell_the_model_what_to_fix(text, msg):
    with pytest.raises(ActionError, match=msg):
        parse_action(text, TOOLS)
