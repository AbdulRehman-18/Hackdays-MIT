"""What the screen agent sees before each step: the front app, the ranked UI element list and a screenshot."""
import os

from ..harness.agent import Observation
from . import ax, screen
from .mac import front_app

SCREENSHOTS = os.getenv("FORGE_SCREENSHOTS", "auto")  # auto: only when the element list is too thin to act on


def permissions() -> dict[str, bool]:
    import Quartz
    try:
        record = bool(Quartz.CGPreflightScreenCaptureAccess())
    except Exception:
        record = True
    return {"accessibility": ax.trusted(), "screen_recording": record}


def observe() -> Observation:
    app = front_app()
    els = ax.elements() if ax.trusted() else []
    jpeg, size = screen.capture()
    lines = [f"Front app: {app or 'unknown'}"]
    if els:
        lines.append("UI elements (use click_element / type_into with the id):")
        lines += [e.line() for e in els]
    else:
        lines.append("UI elements: unavailable here, so use the screenshot and click by coordinates.")
    include = SCREENSHOTS == "always" or (SCREENSHOTS == "auto" and len(els) < 8)
    if include:
        lines.append(f"Screenshot attached: {size[0]}x{size[1]} pixels; coordinates for click use this space.")
    return Observation(text="\n".join(lines), images=[jpeg] if include else [], fingerprint=screen.fingerprint(jpeg),
                       elements={e.id: f"{ax.ROLE_NAMES.get(e.role, e.role)} {e.label}" for e in els}, app=app,
                       focused=ax.focused_label() if ax.trusted() else "")
