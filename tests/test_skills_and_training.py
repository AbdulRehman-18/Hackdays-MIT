import json
from pathlib import Path

from forge.harness.skills import load, parse_frontmatter, validate
from forge.training import export

ROOT = Path(__file__).resolve().parents[1]


def make(tmp, name, fm):
    d = tmp / name
    d.mkdir()
    (d / "SKILL.md").write_text(f"---\n{fm}\n---\n\n# Body\nDo the thing.\n")
    return d


def test_repo_skills_follow_the_spec():
    dirs = [d for d in (ROOT / "skills").iterdir() if d.is_dir()]
    assert len(dirs) >= 5
    for d in dirs:
        assert validate(d) == [], d.name


def test_validate_catches_spec_violations(tmp_path):
    assert validate(make(tmp_path, "good-skill", "name: good-skill\ndescription: Does X. Use when Y.")) == []
    assert any("match the directory" in p for p in validate(make(tmp_path, "dir-a", "name: other\ndescription: d")))
    assert any("lowercase" in p for p in validate(make(tmp_path, "Bad--Name", "name: Bad--Name\ndescription: d")))
    assert any("description" in p for p in validate(make(tmp_path, "no-desc", "name: no-desc")))
    assert validate(tmp_path / "missing") == ["missing SKILL.md"]


def test_frontmatter_with_folded_description_and_metadata():
    fm = parse_frontmatter("---\nname: x\ndescription: >\n  first line\n  second line\nmetadata:\n  author: a\n---\n")
    assert fm["name"] == "x" and fm["description"] == "first line second line"


def test_load_gives_names_descriptions_and_lazy_bodies(tmp_path):
    make(tmp_path, "alpha", "name: alpha\ndescription: Alpha skill.")
    (tmp_path / "broken").mkdir()
    skills = load(tmp_path)
    assert [s.name for s in skills] == ["alpha"]
    assert skills[0].body().startswith("# Body")


def test_export_keeps_only_successful_text_samples(tmp_path):
    def trace(name, finished, images=0):
        sample = {"kind": "sample", "messages": [{"role": "user", "content": "open notes"}], "images": images,
                  "action": {"thought": "t", "tool": "open_app", "args": {"name": "Notes"}}}
        lines = [{"kind": "start"}, sample, {"kind": "end", "finished": finished}]
        (tmp_path / name).write_text("\n".join(json.dumps(x) for x in lines))
    trace("a.jsonl", True)
    trace("b.jsonl", False)
    trace("c.jsonl", True, images=1)
    n, out = export(tmp_path, tmp_path / "out" / "data.jsonl")
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert n == 1 and rows[0]["messages"][-1] == {
        "role": "assistant", "content": '{"thought": "t", "tool": "open_app", "args": {"name": "Notes"}}'}
