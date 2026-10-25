"""The web: open pages, search Google or YouTube, answer live questions with sources, and the weather."""
import datetime
import html
import re
import urllib.parse
from typing import Annotated

import httpx
from pydantic import Field

from .. import runtime
from ..harness.tools import tool
from .mac import run

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) "
                    "Version/17.0 Safari/605.1.15", "Accept-Language": "en-US,en;q=0.9"}


def _open(url: str) -> bool:
    return run("open", url).returncode == 0


@tool(group="web")
def open_url(url: Annotated[str, Field(description="web address, e.g. https://youtube.com")]) -> str:
    """Open a web page in the default browser."""
    url = url.strip()
    if url.startswith("x-apple.systempreferences:"):  # System Settings panes: safe to open, nothing changes
        return f"Opened {url}." if _open(url) else "error: could not open that settings pane"
    if not re.match(r"^https?://", url):
        if re.match(r"^[a-z][a-z0-9+.-]*:", url, re.IGNORECASE):
            return "error: only http and https addresses can be opened"
        url = "https://" + url
    return f"Opened {url}." if _open(url) else f"error: could not open {url}"


@tool(group="web")
def google(query: Annotated[str, Field(description="what to search for")]) -> str:
    """Show Google search results in the browser."""
    _open("https://www.google.com/search?q=" + urllib.parse.quote_plus(query))
    return f"Here are the results for {query}."


@tool(group="web")
def youtube(query: Annotated[str, Field(description="song, video or topic")],
            play: Annotated[bool, Field(description="play the top result (false: just show results)")] = True) -> str:
    """Find a video or song on YouTube and play it."""
    results = "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(query)
    if play:
        try:
            page = httpx.get(results, headers=UA, timeout=8, follow_redirects=True).text
            m = re.search(r'"videoId":"([\w-]{11})"', page)
            if m:
                _open(f"https://www.youtube.com/watch?v={m.group(1)}")
                return f"Playing {query} on YouTube."
        except httpx.HTTPError:
            pass
    _open(results)
    return f"Here's YouTube for {query}."


def search(query: str, n: int = 6) -> list[tuple[str, str, str]]:
    """(title, snippet, url) from DuckDuckGo's HTML endpoint: no key, no tracking."""
    r = httpx.post("https://html.duckduckgo.com/html/", data={"q": query}, headers=UA, timeout=10)
    out = []
    blocks = re.findall(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</a>',
                        r.text, re.S)
    for href, title, snip in blocks[:n]:
        url = urllib.parse.unquote(re.sub(r"^.*?uddg=([^&]+).*$", r"\1", href))
        clean = lambda s: html.unescape(re.sub(r"<[^>]+>", "", s)).strip()  # noqa: E731
        out.append((clean(title), clean(snip), url))
    return out


@tool(group="web")
def web_answer(question: Annotated[str, Field(description="the question, including place and time if any")]) -> str:
    """Answer questions about news, prices, scores, people or anything current, using a live web search."""
    try:
        hits = search(question)
    except httpx.HTTPError as e:
        return f"error: web search failed ({e})"
    if not hits:
        return "I couldn't find anything on the web for that."
    today = datetime.date.today().isoformat()
    ctx = "\n".join(f"[{i}] {t}: {s} ({u})" for i, (t, s, u) in enumerate(hits, 1))
    answer = runtime.ask(f"Today is {today}. Question: {question}\nFresh web results:\n{ctx}\n"
                         "Answer in 1 to 3 short spoken sentences using only these results. No markdown, no URLs. "
                         "If they don't answer it, say so.")
    host = urllib.parse.urlparse(hits[0][2]).netloc.removeprefix("www.")
    return f"{answer} (Source: {host})"


@tool(group="web")
def weather(place: Annotated[str, Field(description="city, or empty for the user's location")] = "") -> str:
    """Current weather and today's forecast."""
    try:
        d = httpx.get(f"https://wttr.in/{urllib.parse.quote(place)}?format=j1", timeout=8).json()
    except (httpx.HTTPError, ValueError):
        return "error: the weather service didn't answer"
    c, day = d["current_condition"][0], d["weather"][0]
    where = place or d.get("nearest_area", [{}])[0].get("areaName", [{}])[0].get("value", "here")
    return (f"In {where} it's {c['temp_C']}°C and {c['weatherDesc'][0]['value'].lower()}, "
            f"with a high of {day['maxtempC']} and a low of {day['mintempC']} today.")


TOOLS = [open_url, google, youtube, web_answer, weather]
