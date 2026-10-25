"""The macOS Accessibility tree: the real buttons, fields and links of the front window, with exact positions.

Small vision models are poor at guessing pixels. Giving them a numbered list of labelled controls turns
"where is the search box?" into "pick #7", which they do well. Needs Accessibility permission."""
import time
from dataclasses import dataclass
from typing import Annotated

from pydantic import Field

from ..harness.tools import tool
from . import screen

ACTIONABLE = {"AXButton", "AXTextField", "AXTextArea", "AXSearchField", "AXLink", "AXCheckBox", "AXRadioButton",
              "AXPopUpButton", "AXMenuButton", "AXComboBox", "AXTab", "AXSlider", "AXSwitch", "AXDisclosureTriangle",
              "AXSecureTextField", "AXCell", "AXImage"}
TEXT_ROLES = {"AXTextField", "AXTextArea", "AXSearchField", "AXComboBox", "AXSecureTextField"}
ROLE_NAMES = {"AXButton": "button", "AXTextField": "text field", "AXTextArea": "text area", "AXSearchField": "search",
              "AXLink": "link", "AXCheckBox": "checkbox", "AXRadioButton": "radio", "AXPopUpButton": "menu",
              "AXMenuButton": "menu", "AXComboBox": "combo box", "AXTab": "tab", "AXSlider": "slider",
              "AXSwitch": "switch", "AXDisclosureTriangle": "expander", "AXSecureTextField": "password field",
              "AXCell": "cell", "AXImage": "image"}
MAX_NODES, MAX_DEPTH = 2500, 40


@dataclass
class Element:
    id: int
    role: str
    label: str
    x: float  # centre, in points
    y: float
    w: float
    h: float
    ref: object
    value: str = ""

    def line(self) -> str:
        sx, sy = screen.to_shot(self.x, self.y)
        val = f" = {self.value[:40]!r}" if self.value and self.role in TEXT_ROLES else ""
        return f"[{self.id}] {ROLE_NAMES.get(self.role, self.role)} {self.label[:60]!r}{val} at ({sx},{sy})"


_latest: list[Element] = []


def _ax():
    import ApplicationServices as AS
    return AS


def trusted() -> bool:
    try:
        return bool(_ax().AXIsProcessTrusted())
    except Exception:
        return False


def _get(el, attr):
    err, v = _ax().AXUIElementCopyAttributeValue(el, attr, None)
    return v if err == 0 else None


def _point(v, kind):
    AS = _ax()
    ok, p = AS.AXValueGetValue(v, kind, None)
    return p if ok else None


def _front_pid() -> int:
    from AppKit import NSWorkspace
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    return int(app.processIdentifier()) if app else 0


def _text(v) -> str:
    return " ".join(str(v).split()) if isinstance(v, str) else ""


def elements(limit: int = 60) -> list[Element]:
    """Visible actionable elements of the front window, ranked so the most useful come first."""
    global _latest
    AS = _ax()
    pid = _front_pid()
    if not pid:
        return []
    app = AS.AXUIElementCreateApplication(pid)
    AS.AXUIElementSetAttributeValue(app, "AXManualAccessibility", True)  # makes Chrome/Electron expose web content
    root = _get(app, "AXFocusedWindow") or _get(app, "AXMainWindow")
    if root is None:
        _latest = []
        return []
    pw, ph = screen.display_points()
    found, stack, seen = [], [(root, 0)], 0
    while stack and seen < MAX_NODES:
        el, depth = stack.pop()
        seen += 1
        role = _get(el, "AXRole") or ""
        if role in ACTIONABLE:
            pos, size = _get(el, "AXPosition"), _get(el, "AXSize")
            p = _point(pos, AS.kAXValueCGPointType) if pos is not None else None
            s = _point(size, AS.kAXValueCGSizeType) if size is not None else None
            if p and s and s.width > 2 and s.height > 2:
                cx, cy = p.x + s.width / 2, p.y + s.height / 2
                if 0 <= cx <= pw and 0 <= cy <= ph:
                    label = (_text(_get(el, "AXTitle")) or _text(_get(el, "AXDescription"))
                             or _text(_get(el, "AXPlaceholderValue")) or _text(_get(el, "AXHelp")))
                    value = _text(_get(el, "AXValue"))
                    if role in ("AXCell", "AXImage") and not label:
                        label = ""
                    if label or role in TEXT_ROLES:
                        found.append(Element(0, role, label or "(no label)", cx, cy, s.width, s.height, el, value))
        if depth < MAX_DEPTH and role not in ("AXTextArea",):
            kids = _get(el, "AXChildren") or []
            stack.extend((k, depth + 1) for k in reversed(list(kids)))
    found.sort(key=lambda e: (e.role not in TEXT_ROLES, e.role in ("AXCell", "AXImage"), e.y, e.x))
    uniq, keys = [], set()
    for e in found:
        k = (e.role, e.label, round(e.x / 8), round(e.y / 8))
        if k not in keys:
            keys.add(k)
            uniq.append(e)
    uniq = sorted(uniq[:limit], key=lambda e: (e.y, e.x))
    for i, e in enumerate(uniq, 1):
        e.id = i
    _latest = uniq
    return uniq


def focused_label() -> str:
    AS = _ax()
    sysw = AS.AXUIElementCreateSystemWide()
    el = _get(sysw, "AXFocusedUIElement")
    if el is None:
        return ""
    return _text(_get(el, "AXTitle")) or _text(_get(el, "AXDescription")) or _text(_get(el, "AXPlaceholderValue"))


def _find(id: int) -> Element:
    for e in _latest:
        if e.id == id:
            return e
    raise ValueError(f"no element #{id} on the current screen; use an id from the latest list")


@tool(group="screen")
def click_element(id: Annotated[int, Field(description="element id from the UI element list")],
                  double: Annotated[bool, Field(description="double click")] = False) -> str:
    """Click a control from the UI element list by its id. Exact, so prefer it over click."""
    e = _find(id)
    if not double and _ax().AXUIElementPerformAction(e.ref, "AXPress") == 0:
        return f"pressed {ROLE_NAMES.get(e.role, e.role)} {e.label!r}"
    screen.click_points(e.x, e.y, double)
    return f"clicked {ROLE_NAMES.get(e.role, e.role)} {e.label!r}"


@tool(group="screen")
def type_into(id: Annotated[int, Field(description="text field id from the UI element list")],
              text: Annotated[str, Field(description="text to put in the field")],
              press_enter: Annotated[bool, Field(description="press Enter afterwards, e.g. to search")] = False) -> str:
    """Replace the text of a field from the UI element list, optionally pressing Enter."""
    e = _find(id)
    AS = _ax()
    screen.click_points(e.x, e.y)
    time.sleep(0.15)
    AS.AXUIElementSetAttributeValue(e.ref, "AXFocused", True)
    if AS.AXUIElementSetAttributeValue(e.ref, "AXValue", text) != 0 or _text(_get(e.ref, "AXValue")) != text:
        screen.press("cmd+a")
        screen.type_string(text)
    if press_enter:
        time.sleep(0.1)
        screen.press("enter")
    return f"typed {text!r} into {e.label!r}" + (" and pressed Enter" if press_enter else "")


TOOLS = [click_element, type_into]
