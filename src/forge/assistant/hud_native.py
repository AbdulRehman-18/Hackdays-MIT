"""The HUD on macOS, ported from Monday's hud.py: static/hud.html (Monday's HUD, unchanged) in a transparent,
click-through, always-on-top window over the main screen.

The window is hidden from screenshots, so the screen agent always sees the real screen, and it never takes mouse
clicks or focus. Forge has its own cursor drawn in the HUD; the user's real cursor is never hidden or customised.
The page is fed through H.* calls at 60 fps from the session's events (`attach`), and a global key monitor gives
Esc (stop), Space (interrupt and listen while Forge is busy), right Option held (push-to-talk) and Ctrl+Option+M
(full screen or Dynamic Island)."""
import json
import threading
import time
from pathlib import Path

HTML = Path(__file__).with_name("static") / "hud.html"
BORDERLESS, ACCESSORY, SHARING_NONE, LEVEL = 0, 1, 0, 1000  # 1000 = NSScreenSaverWindowLevel
ALL_SPACES, STATIONARY, IGNORES_CYCLE, FS_AUX = 1 << 0, 1 << 4, 1 << 6, 1 << 8
KEYDOWN_MASK, KEYUP_MASK, FLAGS_MASK = 1 << 10, 1 << 11, 1 << 12
KEYDOWN_TYPE, KEYUP_TYPE, FLAGS_TYPE = 10, 11, 12  # modifier keys (Option, Command...) only send "flags changed"
ESC, SPACE, M_KEY, RIGHT_OPTION = 53, 49, 46, 61
CTRL, OPT = 1 << 18, 1 << 19
HUD_STATES = {"idle", "listening", "thinking", "acting", "speaking"}
STATE_MAP = {"waiting": "thinking", "transcribing": "thinking"}  # Forge-only states, shown as Monday shows them

_lock = threading.Lock()
_js: list[str] = []
_keep: list = []
_st = {"mode": "idle", "level": 0.0, "sent_level": -1.0, "agent": False, "talking": False}
_setup = {"mode": "full", "nx": 0, "nw": 0, "nh": 32}
_web = None
_ready = False


def _send(js: str):
    with _lock:
        _js.append(js)


# ---- the HUD API (any thread), the same calls Monday's ui.py makes ----
def say(who: str, text: str):
    _send(f"H.say({json.dumps(who)},{json.dumps(str(text))})")


def level(x: float):
    _st["level"] = float(x)


def wake():
    _send("H.wake()")


def click(x: float, y: float):
    _send(f"H.click({float(x)},{float(y)})")


def move(x: float, y: float, ms: int):
    _send(f"H.move({float(x)},{float(y)},{int(ms)})")


def agent(on: bool):
    if bool(on) != _st["agent"]:
        _st["agent"] = bool(on)
        _send(f"H.cursor({'true' if on else 'false'})")


def state(mode: str):
    mode = STATE_MAP.get(mode, mode)
    if mode not in HUD_STATES:
        return
    _st["mode"] = mode
    if not _st["talking"]:
        _send(f"H.state({json.dumps(mode)})")


def speaking(on: bool):
    _st["talking"] = bool(on)
    _send(f"H.state({json.dumps('speaking' if on else _st['mode'])})")


def set_mode(mode: str):
    _setup["mode"] = mode
    _send(f"H.mode({json.dumps(mode)})")


def on_event(msg: dict):
    """Session event -> HUD call. Registered as a session listener by attach()."""
    kind = msg.get("type")
    if kind == "state":
        if msg["state"] == "speaking":
            speaking(True)
        else:
            if _st["talking"]:
                _st["talking"] = False
            state(msg["state"])
    elif kind == "level":
        level(msg.get("v", 0))
    elif kind == "caption":
        say("you" if msg.get("who") == "you" else "monday", msg.get("text", ""))
    elif kind == "wake":
        wake()
    elif kind == "cursor":
        move(msg["x"], msg["y"], msg.get("ms", 0))
    elif kind == "click":
        click(msg["x"], msg["y"])
    elif kind == "agent_cursor":
        agent(bool(msg.get("on")))
    elif kind == "image" and msg.get("jpeg_b64"):
        _send(f"H.image('data:image/jpeg;base64,{msg['jpeg_b64']}')")
    elif kind in ("prefs", "hello") and msg.get("prefs", {}).get("hud_mode") in ("full", "island"):
        if msg["prefs"]["hud_mode"] != _setup["mode"]:
            set_mode(msg["prefs"]["hud_mode"])


def _tick():
    global _ready
    if _web is None or _web.isLoading():
        return
    if not _ready:
        _ready = True
        _web.evaluateJavaScript_completionHandler_(f"window.H&&H.setup({json.dumps(_setup)})", None)
    with _lock:
        parts, _js[:] = _js[:], []
    lv = round(_st["level"], 2)
    if abs(lv - _st["sent_level"]) >= 0.02:
        _st["sent_level"] = lv
        parts.append(f"H.level({lv})")
    if parts:
        _web.evaluateJavaScript_completionHandler_("window.H&&(" + ",".join(parts) + ")", None)


def _notch(scr) -> dict:
    """Notch position and size in points, or zero width on screens without one."""
    try:
        top = scr.safeAreaInsets().top
        left, right = scr.auxiliaryTopLeftArea(), scr.auxiliaryTopRightArea()
        if top > 0 and left.size.width and right.size.width:
            w = scr.frame().size.width - left.size.width - right.size.width
            return {"nx": left.size.width, "nw": w, "nh": top}
    except Exception:
        pass
    return {"nx": 0, "nw": 0, "nh": 32}


def create(session, mode: str = "full"):
    """Create the HUD window and attach it to the session. Must run on the main thread with an NSApplication."""
    global _web
    from AppKit import NSApplication, NSBackingStoreBuffered, NSColor, NSEvent, NSScreen, NSTimer, NSWindow
    from Foundation import NSURL, NSMakeRect, NSObject
    from WebKit import WKWebView, WKWebViewConfiguration

    from ..runtime import self_typing

    class Ticker(NSObject):
        def tick_(self, _timer):
            try:
                _tick()
            except Exception as e:
                print("hud tick error:", e)

    app = NSApplication.sharedApplication()
    scr = NSScreen.screens()[0]
    f = scr.frame()
    _setup.update(_notch(scr), mode=mode)
    win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(f, BORDERLESS, NSBackingStoreBuffered, False)
    win.setOpaque_(False)
    win.setBackgroundColor_(NSColor.clearColor())
    win.setIgnoresMouseEvents_(True)
    win.setHasShadow_(False)
    win.setLevel_(LEVEL)
    win.setCollectionBehavior_(ALL_SPACES | STATIONARY | IGNORES_CYCLE | FS_AUX)
    win.setSharingType_(SHARING_NONE)  # invisible to screenshots, so the agent sees the real screen
    cfg = WKWebViewConfiguration.alloc().init()
    web = WKWebView.alloc().initWithFrame_configuration_(NSMakeRect(0, 0, f.size.width, f.size.height), cfg)
    web.setValue_forKey_(False, "drawsBackground")
    url = NSURL.fileURLWithPath_(str(HTML))
    web.loadFileURL_allowingReadAccessToURL_(url, url.URLByDeletingLastPathComponent())
    win.setContentView_(web)
    win.orderFrontRegardless()
    _web = web
    ticker = Ticker.alloc().init()
    timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(1 / 60, ticker, "tick:", None,
                                                                                     True)

    held, space = [False], [False]

    def run(fn, *a):  # session calls can block (joining a task): keep the main thread free
        threading.Thread(target=fn, args=a, daemon=True).start()

    def key(e):
        code, kind = e.keyCode(), e.type()
        if self_typing():  # Forge's own keystrokes (typing a space, pressing Esc) are not the user's
            return
        if kind == FLAGS_TYPE and code == RIGHT_OPTION:  # push-to-talk: hold right Option, speak, release
            held[0] = not held[0]
            run(session.ptt_key, held[0])
        elif kind == KEYDOWN_TYPE:
            flags = e.modifierFlags()
            if code == M_KEY and flags & CTRL and flags & OPT:
                run(session.toggle_hud)
            elif code == SPACE and not flags & (CTRL | OPT):
                if not space[0]:  # one tap is enough; key repeats are ignored
                    run(session.space_tap)
                space[0] = True
            elif code == ESC and not e.isARepeat():
                run(session.escape)
        elif kind == KEYUP_TYPE and code == SPACE:
            space[0] = False
    mon = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(KEYDOWN_MASK | KEYUP_MASK | FLAGS_MASK, key)
    _keep.extend([app, win, web, cfg, ticker, timer, mon])
    session.listeners.append(on_event)
    session.hud_on = True
    state(session.state)
    return win


def wait_for_session(timeout: float = 30):
    """The session is created by the server thread's startup; wait for it before drawing the HUD."""
    from . import server
    end = time.time() + timeout
    while server.session is None and time.time() < end:
        time.sleep(0.05)
    return server.session


def run():
    """Run the HUD as its own app (used by `forge voice`)."""
    from AppKit import NSApplication
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(ACCESSORY)  # no dock icon, never steals focus
    session = wait_for_session()
    if session is None:
        raise RuntimeError("the Forge session did not start")
    create(session, session.prefs.hud_mode)
    app.run()
