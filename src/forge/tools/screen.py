"""Seeing and touching the screen: capture, coordinate mapping, mouse and keyboard through Quartz events.

The model works in screenshot space (the main display scaled to SHOT_W pixels wide). Everything here maps
those coordinates back to macOS points, which also takes care of Retina displays."""
import hashlib
import io
import math
import time
from typing import Annotated

from pydantic import Field

from ..harness.tools import tool

SHOT_W = 1280
KEYCODES = {
    **{c: k for c, k in zip("asdfhgzxcv", [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], strict=True)},
    "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22,
    "5": 23, "=": 24, "9": 25, "7": 26, "-": 27, "8": 28, "0": 29, "]": 30, "o": 31, "u": 32, "[": 33, "i": 34,
    "p": 35, "enter": 36, "l": 37, "j": 38, "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44, "n": 45,
    "m": 46, ".": 47, "tab": 48, "space": 49, "`": 50, "delete": 51, "backspace": 51, "esc": 53,
    "forwarddelete": 117, "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
    "left": 123, "right": 124, "down": 125, "up": 126,
    **{f"f{i}": k for i, k in zip(range(1, 13), [122, 120, 99, 118, 96, 97, 98, 100, 101, 109, 103, 111], strict=True)},
}
ALIASES = {"return": "enter", "escape": "esc", "command": "cmd", "meta": "cmd", "super": "cmd", "control": "ctrl",
           "opt": "option", "alt": "option", "del": "delete", "arrowup": "up", "arrowdown": "down",
           "arrowleft": "left", "arrowright": "right", "page_down": "pagedown", "page_up": "pageup"}


def _q():
    import Quartz
    return Quartz


def display_points() -> tuple[float, float]:
    Q = _q()
    b = Q.CGDisplayBounds(Q.CGMainDisplayID())
    return b.size.width, b.size.height


def capture() -> tuple[bytes, tuple[int, int]]:
    """JPEG of the main display at SHOT_W wide, and its size. Needs Screen Recording permission."""
    from PIL import Image
    Q = _q()
    img = Q.CGDisplayCreateImage(Q.CGMainDisplayID())
    if img is None:
        raise RuntimeError("screen capture failed: allow Screen Recording for this app in System Settings")
    w, h = Q.CGImageGetWidth(img), Q.CGImageGetHeight(img)
    data = Q.CGDataProviderCopyData(Q.CGImageGetDataProvider(img))
    stride = Q.CGImageGetBytesPerRow(img)
    pil = Image.frombuffer("RGBA", (w, h), bytes(data), "raw", "BGRA", stride, 1).convert("RGB")
    pw, ph = display_points()
    size = (SHOT_W, round(SHOT_W * ph / pw))
    pil = pil.resize(size, Image.LANCZOS)
    buf = io.BytesIO()
    pil.save(buf, "JPEG", quality=70)
    return buf.getvalue(), size


def fingerprint(jpeg: bytes) -> str:
    """A coarse hash that changes when the screen visibly changes, but not for a blinking cursor."""
    from PIL import Image
    small = Image.open(io.BytesIO(jpeg)).convert("L").resize((48, 30))
    return hashlib.md5(bytes(v // 16 for v in small.tobytes())).hexdigest()


def to_points(x: float, y: float) -> tuple[float, float]:
    pw, _ = display_points()
    s = pw / SHOT_W
    return x * s, y * s


def to_shot(x: float, y: float) -> tuple[int, int]:
    pw, _ = display_points()
    s = SHOT_W / pw
    return round(x * s), round(y * s)


def _post(ev):
    Q = _q()
    Q.CGEventPost(Q.kCGHIDEventTap, ev)


def mouse_location() -> tuple[float, float]:
    Q = _q()
    p = Q.CGEventGetLocation(Q.CGEventCreate(None))
    return p.x, p.y


_cursor: list[tuple[float, float] | None] = [None]  # where Forge's own pointer is on the HUD; None = in the orb


def show_cursor(on: bool):
    """Forge's cursor pops out of the orb (on) or flies back into it (off). The user's cursor is never touched."""
    from .. import runtime
    if not on:
        _cursor[0] = None
    runtime.emit("agent_cursor", {"on": on})


def glide(x: float, y: float):
    """Fly Forge's cursor to (x, y) along a curve (280-850 ms by distance) and wait until it lands there."""
    from .. import runtime
    if _cursor[0] is None:
        show_cursor(True)
        time.sleep(0.25)  # let it pop out of the orb first
        pw, ph = display_points()
        x0, y0 = pw / 2, ph - 84
    else:
        x0, y0 = _cursor[0]
    d = math.hypot(x - x0, y - y0)
    ms = 0 if d < 3 else int(min(850, 280 + d * 0.32))
    runtime.emit("cursor", {"x": x, "y": y, "ms": ms})
    _cursor[0] = (x, y)
    time.sleep(ms / 1000 + 0.06)


class borrow:
    """Use the real cursor for one action, then put it back exactly where the user left it."""

    def __enter__(self):
        self.home = mouse_location()
        return self

    def __exit__(self, *exc):
        Q = _q()
        Q.CGWarpMouseCursorPosition(self.home)
        Q.CGAssociateMouseAndMouseCursorPosition(True)


def click_points(x: float, y: float, double: bool = False):
    from .. import runtime
    Q = _q()
    pt = (x, y)
    glide(x, y)
    with borrow():
        _post(Q.CGEventCreateMouseEvent(None, Q.kCGEventMouseMoved, pt, Q.kCGMouseButtonLeft))
        time.sleep(0.03)
        for n in (1, 2) if double else (1,):
            for kind in (Q.kCGEventLeftMouseDown, Q.kCGEventLeftMouseUp):
                ev = Q.CGEventCreateMouseEvent(None, kind, pt, Q.kCGMouseButtonLeft)
                Q.CGEventSetIntegerValueField(ev, Q.kCGMouseEventClickState, n)
                _post(ev)
                time.sleep(0.03)
    runtime.emit("click", {"x": x, "y": y})


def drag_path(points: list[tuple[float, float]], step: float = 6.0):
    """Press, follow the points (filled in every few points so strokes are smooth), release."""
    from .. import runtime
    Q = _q()
    if len(points) < 2:
        return
    dense = [points[0]]
    for (ax, ay), (bx, by) in zip(points, points[1:], strict=False):
        n = max(1, int(((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5 / step))
        dense += [(ax + (bx - ax) * i / n, ay + (by - ay) * i / n) for i in range(1, n + 1)]
    glide(*dense[0])
    with borrow():
        _post(Q.CGEventCreateMouseEvent(None, Q.kCGEventLeftMouseDown, dense[0], Q.kCGMouseButtonLeft))
        for i, p in enumerate(dense[1:]):
            _post(Q.CGEventCreateMouseEvent(None, Q.kCGEventLeftMouseDragged, p, Q.kCGMouseButtonLeft))
            if i % 4 == 0:
                runtime.emit("cursor", {"x": p[0], "y": p[1], "ms": 0})
            time.sleep(0.008)
        _post(Q.CGEventCreateMouseEvent(None, Q.kCGEventLeftMouseUp, dense[-1], Q.kCGMouseButtonLeft))
    time.sleep(0.05)


def type_string(text: str):
    from .. import runtime
    Q = _q()
    for i in range(0, len(text), 16):
        runtime.typed_by_me()
        chunk = text[i:i + 16]
        for down in (True, False):
            ev = Q.CGEventCreateKeyboardEvent(None, 0, down)
            Q.CGEventKeyboardSetUnicodeString(ev, len(chunk), chunk)
            _post(ev)
        time.sleep(0.02)
    runtime.typed_by_me()


def press(combo: str):
    Q = _q()
    parts = [ALIASES.get(p, p) for p in combo.lower().replace(" ", "").split("+") if p]
    flags = 0
    masks = {"cmd": Q.kCGEventFlagMaskCommand, "shift": Q.kCGEventFlagMaskShift,
             "option": Q.kCGEventFlagMaskAlternate, "ctrl": Q.kCGEventFlagMaskControl}
    key = None
    for p in parts:
        if p in masks:
            flags |= masks[p]
        else:
            key = p
    if key not in KEYCODES:
        raise ValueError(f"unknown key {key!r} in {combo!r}")
    from .. import runtime
    for down in (True, False):
        runtime.typed_by_me()
        ev = Q.CGEventCreateKeyboardEvent(None, KEYCODES[key], down)
        Q.CGEventSetFlags(ev, flags)
        _post(ev)
        time.sleep(0.02)
    runtime.typed_by_me()


@tool(group="screen")
def click(x: Annotated[int, Field(description="x in screenshot pixels")],
          y: Annotated[int, Field(description="y in screenshot pixels")],
          double: Annotated[bool, Field(description="double click")] = False) -> str:
    """Click at screenshot coordinates. Only when the target is not in the UI element list."""
    click_points(*to_points(x, y), double)
    return f"clicked at {x},{y}"


@tool(group="screen")
def type_text(text: Annotated[str, Field(description="text to type")],
              press_enter: Annotated[bool, Field(description="press Enter afterwards")] = False) -> str:
    """Type text into whatever field has focus."""
    type_string(text)
    if press_enter:
        press("enter")
    return f"typed {text!r}" + (" and pressed Enter" if press_enter else "")


@tool(group="screen")
def key(combo: Annotated[str, Field(description="key or shortcut, e.g. enter, esc, cmd+l, cmd+shift+t")]) -> str:
    """Press a key or a keyboard shortcut."""
    press(combo)
    return f"pressed {combo}"


@tool(group="screen")
def scroll(direction: Annotated[str, Field(description="up or down")],
           amount: Annotated[int, Field(description="lines, 1 to 20")] = 6) -> str:
    """Scroll the page under the mouse."""
    Q = _q()
    n = max(1, min(20, amount)) * (1 if direction.lower().startswith("u") else -1)
    _post(Q.CGEventCreateScrollWheelEvent(None, Q.kCGScrollEventUnitLine, 1, n))
    return f"scrolled {direction}"


@tool(group="screen")
def wait(seconds: Annotated[float, Field(description="seconds, at most 10")] = 2) -> str:
    """Wait for the screen to finish loading or updating."""
    time.sleep(max(0.2, min(10.0, seconds)))
    return f"waited {seconds}s"


TOOLS = [click, type_text, key, scroll, wait]
