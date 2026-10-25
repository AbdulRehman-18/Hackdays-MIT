"""Opening apps: one call does the whole job, no clicking, and it checks that the app really came to the front."""
import sys
import time
from typing import Annotated

from pydantic import Field

from ..harness.tools import tool
from .mac import front_app, run
from .web import open_url

BROWSER_WORDS = {"browser", "web browser", "internet", "default browser"}


def _wait_front(name: str, seconds: float = 4.0) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if name.lower() in front_app().lower():
            return True
        time.sleep(0.25)
    return False


@tool(group="apps")
def open_app(name: Annotated[str, Field(description="app name, e.g. Safari, Notes, Calculator, or 'browser'")]) -> str:
    """Open an application by name and bring it to the front."""
    if name.strip().lower() in BROWSER_WORDS:
        return open_url.fn("https://www.google.com")
    r = run("open", "-a", name)
    if r.returncode != 0:
        return f"error: no app called {name!r} is installed"
    if sys.platform == "win32":
        return f"{name} is open."
    if _wait_front(name):
        return f"{name} is open."
    return f"{name} was launched but is not in front yet (front app: {front_app() or 'unknown'})."


@tool(group="apps", risk="high")
def quit_app(name: Annotated[str, Field(description="app name")]) -> str:
    """Quit an application (unsaved work could be lost, so this always asks first)."""
    from .mac import osa, q
    osa(f"tell application {q(name)} to quit")
    return f"Quit {name}."


TOOLS = [open_app, quit_app]
