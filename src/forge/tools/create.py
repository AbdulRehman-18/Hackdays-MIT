"""Things Forge makes for you: study notes from a document, and spreadsheets scraped from a web page.
Everything is written under ~/Documents/Forge Notes/ and opened when it is ready."""
import csv
import datetime
import re
from pathlib import Path
from typing import Annotated

import httpx
from pydantic import Field

from .. import runtime
from ..harness.backends import Message
from ..harness.tools import tool
from .files import resolve, spotlight
from .mac import front_app, osa, q, run
from .research import page_text
from .web import UA

NOTES = Path.home() / "Documents" / "Forge Notes"
CHROMIUM = ("Google Chrome", "Arc", "Brave Browser", "Microsoft Edge", "Chromium", "Vivaldi", "Opera")

STUDY = """You are an expert tutor. Turn the document below into a complete Markdown study guide.
Use exactly these sections:
# {title} — Study Notes
## Summary            (5-8 sentences)
## Key concepts       (a bullet per concept: **term** — plain-language explanation)
## Important facts    (a Markdown table: Fact | Detail)
## Multiple choice    (8 questions, options A-D)
## Short answer       (5 questions)
## Long answer        (2 questions)
## Answer key         (answers to every question above, numbered)
## Revision checklist (6-10 "- [ ]" items)
{focus}Only use information from the document. Write the guide now, nothing before or after it.

Document "{title}":
---
{text}
---"""


def _slug(s: str) -> str:
    return re.sub(r"[^\w\- ]+", "", s).strip()[:60] or "notes"


def _find_document(query: str) -> Path:
    try:
        return resolve(query)
    except FileNotFoundError:
        docs = [p for p in spotlight(query, 20) if p.suffix.lower() in (".pdf", ".docx", ".txt", ".md", ".pptx")]
        if not docs:
            raise
        return docs[0]


@tool(group="create")
def study_notes(query: Annotated[str, Field(description="the document's name or topic words")],
                focus: Annotated[str, Field(description="topic to emphasise, or empty")] = "") -> str:
    """Write a study guide file from a PDF or document (summary, concepts, quiz, answer key). Only when the user
    asks for study notes, questions or a quiz; to just summarise a file use read_file."""
    from .files import read_text
    src = _find_document(query)
    text = read_text(src, 24000)
    if len(text.strip()) < 200:
        return f"error: {src.name} has almost no readable text (it may be a scan)"
    runtime.emit("progress", {"text": f"Reading {src.name} and writing your study notes…"})
    prompt = STUDY.format(title=src.stem, text=text, focus=f"Give extra attention to: {focus}.\n" if focus else "")
    md = runtime.fast.complete([Message("user", prompt)], temperature=0.4).text.strip()
    md = re.sub(r"^```(?:markdown)?\s*|\s*```$", "", md)
    NOTES.mkdir(parents=True, exist_ok=True)
    out = NOTES / f"{_slug(src.stem)} — Study Notes.md"
    out.write_text(md + f"\n\n---\n*Made by Forge from `{src.name}` on {datetime.date.today():%d %B %Y}.*\n")
    run("open", str(out))
    return f"Study notes for {src.name} are ready and open: {out}"


def front_tab_url() -> str:
    app = front_app()
    if app == "Safari":
        return osa('tell application "Safari" to get URL of front document')
    for name in CHROMIUM:
        if name.lower() in app.lower():
            return osa(f"tell application {q(name)} to get URL of active tab of front window")
    raise RuntimeError("the front window isn't a browser I can read; give me the site's address instead")


SCRAPE_SCHEMA = {"type": "object", "properties": {
    "title": {"type": "string"},
    "columns": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 8},
    "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}, "maxItems": 60},
}, "required": ["title", "columns", "rows"]}


@tool(group="create")
def scrape(target: Annotated[str, Field(description="web address or site name; empty for the page on screen")] = "",
           what: Annotated[str, Field(description="the data to pull out, e.g. 'product names and prices'")]
           = "the main data as a table") -> str:
    """Pull data off a web page into a spreadsheet (CSV) and a Markdown table, saved in Forge Notes."""
    import json
    url = target.strip() or front_tab_url()
    if not url.startswith("http"):
        url = "https://" + (url if "." in url else f"{url}.com")
    try:
        r = httpx.get(url, headers=UA, timeout=15, follow_redirects=True)
    except httpx.HTTPError as e:
        return f"error: couldn't load {url} ({e})"
    title, text = page_text(r.text, 14000)
    if len(text) < 200:
        return "error: that page builds itself with JavaScript, so there's nothing to read without a browser"
    c = runtime.fast.complete([Message("user", f"Extract {what} from this web page as a table. Use only data that "
                                               f"is on the page.\nPage: {title} ({url})\n---\n{text}")],
                              schema=SCRAPE_SCHEMA, temperature=0.1)
    data = json.loads(c.text)
    cols, rows = data["columns"], [r_[:len(data["columns"])] for r_ in data["rows"] if r_]
    if not rows:
        return f"I couldn't find {what} on {title or url}."
    folder = NOTES / "Scrapes"
    folder.mkdir(parents=True, exist_ok=True)
    base = folder / f"{_slug(data['title'] or title)} {datetime.datetime.now():%Y-%m-%d %H.%M}"
    with base.with_suffix(".csv").open("w", newline="") as f:
        csv.writer(f).writerows([cols, *rows])
    md = [f"# {data['title'] or title}", "", f"Source: {url}", "", "| " + " | ".join(cols) + " |",
          "|" + "---|" * len(cols)] + ["| " + " | ".join(x.replace("|", "/") for x in r_) + " |" for r_ in rows]
    base.with_suffix(".md").write_text("\n".join(md) + "\n")
    run("open", str(base.with_suffix(".md")))
    return f"Scraped {len(rows)} rows ({', '.join(cols)}) from {title or url}. Saved as {base.name}.csv and .md."


TOOLS = [study_notes, scrape]
