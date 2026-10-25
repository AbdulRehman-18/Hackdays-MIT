"""Apple apps through AppleScript: Reminders (syncs to the iPhone), Notes, Calendar, plus built-in timers.

Dates are built field by field inside AppleScript, never parsed from text, so they work in any system locale."""
import datetime
import re
import threading
import time
from typing import Annotated

from pydantic import Field

from .. import runtime
from ..harness.tools import tool
from .mac import notify, osa, q

WHEN = "date and time as YYYY-MM-DD HH:MM (24-hour), or empty"


def _parse(when: str) -> datetime.datetime | None:
    when = (when or "").strip()
    if not when:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(when, fmt)
        except ValueError:
            pass
    raise ValueError(f"I need the time as YYYY-MM-DD HH:MM, got {when!r}")


def _date_script(var: str, d: datetime.datetime) -> str:
    return (f"set {var} to current date\nset day of {var} to 1\nset year of {var} to {d.year}\n"
            f"set month of {var} to {d.month}\nset day of {var} to {d.day}\n"
            f"set time of {var} to {d.hour * 3600 + d.minute * 60}\n")


def _say_when(d: datetime.datetime) -> str:
    day = "today" if d.date() == datetime.date.today() else (
        "tomorrow" if d.date() == datetime.date.today() + datetime.timedelta(days=1) else d.strftime("%A %B %-d"))
    return f"{day} at {d.strftime('%-I:%M %p')}"


@tool(group="apple")
def add_reminder(text: Annotated[str, Field(description="what to remember")],
                 when: Annotated[str, Field(description=WHEN)] = "",
                 in_minutes: Annotated[int, Field(description="minutes from now, or 0")] = 0) -> str:
    """Add an Apple Reminder (syncs to the user's iPhone)."""
    d = datetime.datetime.now() + datetime.timedelta(minutes=in_minutes) if in_minutes > 0 else _parse(when)
    props = f"{{name:{q(text)}" + (", remind me date:d}" if d else "}")
    osa((_date_script("d", d) if d else "") +
        f'tell application "Reminders" to make new reminder at end of default list with properties {props}')
    return f"I'll remind you to {text}" + (f" {_say_when(d)}." if d else ".")


@tool(group="apple")
def add_note(title: Annotated[str, Field(description="short title")],
             text: Annotated[str, Field(description="the note's content")]) -> str:
    """Save a note in Apple Notes."""
    body = "<h1>" + title + "</h1>" + "".join(f"<div>{line}</div>" for line in text.splitlines() or [""])
    osa(f'tell application "Notes" to make new note with properties {{body:{q(body)}}}')
    return f"Saved the note {title!r}."


@tool(group="apple")
def add_event(title: Annotated[str, Field(description="event title")],
              start: Annotated[str, Field(description="start as YYYY-MM-DD HH:MM (24-hour)")],
              minutes: Annotated[int, Field(description="length in minutes")] = 60) -> str:
    """Add an event to Apple Calendar."""
    d = _parse(start)
    if d is None:
        return "error: I need a start time"
    osa(_date_script("s", d) + f"set e to s + ({minutes} * minutes)\n"
        'tell application "Calendar"\n'
        "set cal to first calendar whose writable is true\n"
        f"make new event at end of events of cal with properties {{summary:{q(title)}, start date:s, end date:e}}\n"
        "end tell")
    return f"Added {title} to your calendar, {_say_when(d)}."


@tool(group="apple")
def calendar(day_offset: Annotated[int, Field(description="0 today, 1 tomorrow")] = 0) -> str:
    """Read the user's calendar events for a day."""
    d = datetime.datetime.combine(datetime.date.today() + datetime.timedelta(days=day_offset), datetime.time())
    out = osa(_date_script("a", d) + "set b to a + (1 * days)\nset out to \"\"\n"
              'tell application "Calendar"\nrepeat with c in calendars\n'
              "repeat with e in (every event of c whose start date ≥ a and start date < b)\n"
              'set out to out & (time string of (start date of e)) & "|" & (summary of e) & linefeed\n'
              "end repeat\nend repeat\nend tell\nreturn out", timeout=40)
    rows = sorted(line.split("|", 1) for line in out.splitlines() if "|" in line)
    day = "today" if day_offset == 0 else "tomorrow" if day_offset == 1 else d.strftime("%A")
    if not rows:
        return f"Your calendar is clear {day}."
    items = [f"{re.sub(r':00(?= )', '', t.strip())} {s.strip()}" for t, s in rows[:6]]
    return f"You have {len(rows)} event{'s' if len(rows) > 1 else ''} {day}: " + "; ".join(items) + "."


_timers: list[tuple[float, str, threading.Timer]] = []  # (end time, name, timer)
UNITS = {"second": 1, "sec": 1, "minute": 60, "min": 60, "hour": 3600, "hr": 3600}


def parse_duration(text: str) -> int:
    """'10 minutes', '1 hour 30 minutes', 'an hour and a half', '90 seconds' -> seconds."""
    t = text.lower().replace("an hour and a half", "90 minutes").replace("half an hour", "30 minutes")
    t = re.sub(r"\b(a|an|one)\s+(?=(second|sec|minute|min|hour|hr))", "1 ", t)
    return int(sum(float(n) * UNITS[u] for n, u in
                   re.findall(r"(\d+(?:\.\d+)?)\s*(second|sec|minute|min|hour|hr)s?\b", t)))


def say_duration(sec: int) -> str:
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    parts = [f"{h} hour{'s' * (h != 1)}" if h else "", f"{m} minute{'s' * (m != 1)}" if m else "",
             f"{s} second{'s' * (s != 1)}" if s and not h else ""]
    return " ".join(p for p in parts if p) or "0 seconds"


def _ring(name: str, th_ref: list):
    _timers[:] = [x for x in _timers if x[2] is not th_ref[0]]
    notify("Monday timer", f"{name} timer is done!")
    runtime.emit("timer", {"label": name, "say": f"Ding! Your {name} timer is done!"})


@tool(group="apple")
def set_timer(seconds: Annotated[int, Field(description="duration in seconds")],
              label: Annotated[str, Field(description="what it is for, or empty")] = "") -> str:
    """Start a countdown timer that announces itself when it ends."""
    if seconds <= 0:
        return "How long should the timer be?"
    name = label or say_duration(seconds)
    ref: list = []
    th = threading.Timer(seconds, _ring, args=(name, ref))
    ref.append(th)
    th.daemon = True
    th.start()
    _timers.append((time.time() + seconds, name, th))
    return f"Timer set for {say_duration(seconds)}!"


def timers_left() -> str:
    live = [(end, name) for end, name, t in _timers if t.is_alive()]
    if not live:
        return "You don't have any timers running."
    return " ".join(f"Your {name} timer has {say_duration(max(0, int(end - time.time())))} left."
                    for end, name in sorted(live))


def cancel_timers() -> str:
    n = len(_timers)
    for _, _, th in _timers:
        th.cancel()
    _timers.clear()
    return "Timer cancelled." if n == 1 else f"Cancelled {n} timers." if n else "There's no timer running."


TOOLS = [add_reminder, add_note, add_event, calendar, set_timer]
