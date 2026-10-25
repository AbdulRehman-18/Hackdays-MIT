import json

import pytest

from forge.harness.backends import Completion, Delta
from forge.harness.chat import ChatAgent, Turn
from forge.harness.policy import Policy
from forge.harness.schema import Action
from forge.tools import code


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "calc.py").write_text('def average(numbers):\n    """Mean."""\n'
                                      "    return sum(numbers) / len(numbers)\n")
    (tmp_path / "test_calc.py").write_text("from calc import average\n\n"
                                           "def test_empty():\n    assert average([]) == 0\n")
    return tmp_path


def tools(ws):
    return {t.name: t for t in code.workspace_tools(ws)}


def run(t, **args):
    return t.run_full(t.args.model_validate(args))


def test_paths_cannot_escape_the_project(ws):
    with pytest.raises(ValueError, match="outside the project"):
        code.inside(ws, "../../etc/passwd")
    assert code.inside(ws, "calc.py") == (ws / "calc.py").resolve()


def test_read_numbers_lines_and_edit_returns_a_diff(ws):
    t = tools(ws)
    text, extras = run(t["read_file"], path="calc.py")
    assert "    3\t    return sum(numbers)" in text and extras["path"] == "calc.py"
    text, extras = run(t["edit_file"], path="calc.py", old_text="return sum(numbers) / len(numbers)",
                       new_text="if not numbers:\n    return 0\nreturn sum(numbers) / len(numbers)")
    assert text.startswith("Edited calc.py (+2 -0)") and "+    if not numbers:" in extras["diff"]
    # the copied text had no indentation: the new lines got the original's 4 spaces
    assert "    if not numbers:\n        return 0\n    return sum" in (ws / "calc.py").read_text()


def test_edit_needs_exact_unique_text(ws):
    t = tools(ws)
    with pytest.raises(ValueError, match="not found"):
        run(t["edit_file"], path="calc.py", old_text="return mean(numbers)", new_text="x")
    (ws / "dup.py").write_text("a = 1\na = 1\n")
    with pytest.raises(ValueError, match="appears 2 times"):
        run(t["edit_file"], path="dup.py", old_text="a = 1", new_text="a = 2")
    text, _ = run(t["write_file"], path="dup.py", content="a = 1\na = 1\n")
    assert text.startswith("No change")


def test_command_output_and_exit_code(ws):
    text, extras = run(tools(ws)["run_command"], command="echo hi && exit 3")
    assert extras["exit_code"] == 3 and "hi" in text


def test_policy_for_code_tools(ws):
    t, p = tools(ws), Policy()
    assert p.check(Action("run_command", {"command": "git status"}), t["run_command"]).decision == "allow"
    assert p.check(Action("run_command", {"command": "pytest -q"}), t["run_command"]).decision == "confirm"
    assert p.check(Action("run_command", {"command": "rm -rf ~"}), t["run_command"]).decision == "block"
    assert p.check(Action("run_command", {"command": "ls; rm x"}), t["run_command"]).decision == "confirm"
    assert p.check(Action("edit_file", {"path": "a.py"}), t["edit_file"]).decision == "confirm"


def test_preview_shows_the_diff_before_approval(ws):
    diff = code.preview(ws, "edit_file", {"path": "calc.py", "old_text": '"""Mean."""', "new_text": '"""Average."""'})
    assert '-    """Mean."""' in diff and '+    """Average."""' in diff


def test_finish_check_refuses_to_stop_early():
    failing = [{"tool": "run_command", "status": "error", "exit_code": 1}]
    assert "failed" in code.finish_check(failing, "fix the tests")
    assert "no file has been edited" in code.finish_check([], "add a login page")
    fixed = failing + [{"tool": "edit_file", "status": "ok"}, {"tool": "run_command", "status": "ok", "exit_code": 0}]
    assert code.finish_check(fixed, "fix the tests") == ""
    assert "Look at the project" in code.finish_check([], "explain this project")  # never answer unread
    read = [{"tool": "read_file", "status": "ok"}]
    assert code.finish_check(read, "explain how calc.py works") == ""


def test_orientation_and_test_command(ws):
    o = code.orientation(ws)
    assert "calc.py" in o and "test_calc.py" in o and "Run the tests with:" in o


class Scripted:
    name, model = "fake", "scripted"

    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []

    def complete(self, messages, schema=None, temperature=0.2):
        self.prompts.append(messages[-1].content)
        return Completion(self.replies.pop(0), 0.01, 1, 1, True)

    def stream(self, messages, think=False, temperature=0.6):
        yield Delta("text", "summary")
        yield Delta("done")


def j(tool_, **args):
    return json.dumps({"thought": "", "tool": tool_, "args": args})


def test_cycle_guard_stops_a_repeated_call(ws):
    same = j("run_command", command="false")
    b = Scripted(same, same, same, same, j("respond"))
    r = ChatAgent(b, code.workspace_tools(ws), approve=lambda a, v: True, plan=False).run([], Turn("user", "run it"))
    assert r.steps[-1]["status"] == "skip" and "repeated" in r.steps[-1]["result"]
    assert "3 times" in b.prompts[3]  # warned before it was stopped


def test_pushback_note_reaches_the_model_without_a_plan(ws):
    b = Scripted(j("run_command", command="false"), j("respond"), j("respond"), j("respond"))
    ChatAgent(b, code.workspace_tools(ws), approve=lambda a, v: True, plan=False,
              finish_check=code.finish_check).run([], Turn("user", "fix it"))
    assert "The last command failed" in b.prompts[2]
