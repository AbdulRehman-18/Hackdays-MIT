"""Live information for the chat agent: web search, reading pages, and the news.

These return raw material (titles, snippets, page text) instead of finished answers: the agent reads it in
its scratchpad and writes one grounded reply that names its sources."""
import html
import re
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Annotated

import httpx
from pydantic import Field

from ..harness.tools import tool
from .web import UA, search


def page_text(raw: str, limit: int = 7000) -> tuple[str, str]:
    """(title, readable text) from HTML: scripts, styles and navigation removed, table rows kept as lines."""
    title = html.unescape(re.search(r"<title[^>]*>(.*?)</title>", raw, re.S | re.I).group(1).strip()) \
        if re.search(r"<title[^>]*>(.*?)</title>", raw, re.S | re.I) else ""
    body = re.search(r"<(article|main)\b.*?</\1>", raw, re.S | re.I)
    s = body.group(0) if body and len(body.group(0)) > 2000 else raw
    s = re.sub(r"<(script|style|noscript|svg|nav|footer|header|form|iframe)\b.*?</\1>", " ", s, flags=re.S | re.I)
    s = re.sub(r"</(td|th)>", " | ", s, flags=re.I)
    s = re.sub(r"<(br|/p|/div|/li|/tr|/h\d)\b[^>]*>", "\n", s, flags=re.I)
    s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    lines = [" ".join(line.split()) for line in s.splitlines()]
    text = "\n".join(line for line in lines if len(line) > 2)
    return title, text[:limit]


@tool(group="search")
def web_search(query: Annotated[str, Field(description="search words, with place and time if relevant")]) -> str:
    """Search the web (DuckDuckGo). Returns titles, snippets and links to read."""
    try:
        hits = search(query, 8)
    except httpx.HTTPError as e:
        return f"error: web search failed ({e})"
    if not hits:
        return "No results."
    return ("\n".join(f"[{i}] {t} — {s} ({u})" for i, (t, s, u) in enumerate(hits, 1))
            + "\n(Snippets are short. Unless one already answers a quick fact, read the best one or two pages "
              "with read_webpage before you respond.)")


@tool(group="search")
def read_webpage(url: Annotated[str, Field(description="full https:// address from the search results")]) -> str:
    """Read the main text of a web page."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        r = httpx.get(url, headers=UA, timeout=12, follow_redirects=True)
    except httpx.HTTPError as e:
        return f"error: could not load {url} ({e})"
    if "html" not in r.headers.get("content-type", "html"):
        return f"error: {url} is not a web page ({r.headers.get('content-type')})"
    title, text = page_text(r.text)
    if len(text) < 200:
        return f"{title}: the page builds itself with JavaScript, so there is little text to read here."
    return f"{title} ({urllib.parse.urlparse(url).netloc})\n{text}"


@tool(group="search")
def news(topic: Annotated[str, Field(description="topic, or empty for top headlines")] = "") -> str:
    """Latest news headlines (Google News), optionally about a topic."""
    q = f"search?q={urllib.parse.quote(topic)}&" if topic else "?"
    try:
        r = httpx.get(f"https://news.google.com/rss/{q}hl=en-US&gl=US&ceid=US:en", headers=UA, timeout=10)
        items = ET.fromstring(r.content).findall("./channel/item")[:8]
    except (httpx.HTTPError, ET.ParseError) as e:
        return f"error: the news feed didn't answer ({e})"
    if not items:
        return "No news found."
    out = []
    for it in items:
        src = it.find("source")
        when = (it.findtext("pubDate") or "")[:16]
        out.append(f"- {it.findtext('title')} ({src.text if src is not None else 'news'}, {when})")
    return "\n".join(out)


TOOLS = [web_search, read_webpage, news]
