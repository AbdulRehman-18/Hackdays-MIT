"""The Mac itself: battery, volume, media keys, screenshots, locking, and reading what is on screen."""
import datetime
import re
from pathlib import Path
from typing import Annotated

from pydantic import Field

from .. import runtime
from ..harness.tools import tool
from . import screen
from .mac import front_app, osa, run


@tool(group="system")
def battery() -> str:
    """Battery level and whether it is charging."""
    out = run("pmset", "-g", "batt").stdout
    m = re.search(r"(\d+)%;\s*([\w ]+)", out)
    if not m:
        return "This Mac has no battery."
    pct, state = m.groups()
    return f"Battery is at {pct} percent" + (", charging." if state.strip() in ("charging", "charged") else ".")


@tool(group="system")
def volume(level: Annotated[int, Field(description="0 to 100; use -1 to mute")] = 50) -> str:
    """Set the output volume, or mute with -1."""
    if level < 0:
        osa("set volume output muted true")
        return "Muted."
    level = max(0, min(100, level))
    osa(f"set volume output volume {level} without output muted")
    return f"Volume {level} percent."


def change_volume(delta: int) -> str:
    cur = int(osa("output volume of (get volume settings)") or 50)
    return volume.fn(max(0, min(100, cur + delta)))


MEDIA_KEYS = {"playpause": 16, "next": 17, "previous": 18}


@tool(group="system")
def media(action: Annotated[str, Field(description="playpause, next or previous")] = "playpause") -> str:
    """Press a media key: play/pause, next track or previous track."""
    import Quartz
    from AppKit import NSEvent
    code = MEDIA_KEYS.get(action, 16)
    for down in (True, False):
        ev = NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
            14, (0, 0), 0xA00 if down else 0xB00, 0, 0, None, 8, (code << 16) | ((0xA if down else 0xB) << 8), -1)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev.CGEvent())
    return {"playpause": "Done.", "next": "Next track.", "previous": "Previous track."}.get(action, "Done.")


@tool(group="system")
def take_screenshot() -> str:
    """Save a screenshot of the whole screen to the Desktop."""
    p = Path.home() / "Desktop" / f"Screenshot {datetime.datetime.now():%Y-%m-%d at %H.%M.%S}.png"
    r = run("screencapture", "-x", str(p))
    return "Screenshot saved to your Desktop." if r.returncode == 0 else "error: screenshot failed"


@tool(group="system")
def lock_screen() -> str:
    """Lock the Mac (turns the display off; the password is needed to come back)."""
    run("pmset", "displaysleepnow")
    return "Locked."


@tool(group="system")
def read_screen(question: Annotated[str, Field(description="what the user wants to know about the screen")]
                = "What is on my screen?") -> str:
    """Look at the screen and answer a question about it: read text, explain code, an error, a page."""
    jpeg, _ = screen.capture()
    return runtime.ask(f"This is the user's screen (front app: {front_app()}). They ask: {question}\n"
                       "Answer in at most 3 short sentences that sound natural spoken aloud. No markdown.", [jpeg])


TOOLS = [battery, volume, media, take_screenshot, lock_screen, read_screen]
