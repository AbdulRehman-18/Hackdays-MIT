"""The HUD on Windows: a small always-on-top card above the taskbar, drawn by static/hud_win.html in a pywebview
(WebView2) window.

macOS gets a full-screen transparent overlay; on Windows that would mean compositing a whole screen of WebView2 for a
few pixels of orb, so this is a small card instead. It shares the HUD API of `hud_native` (the session's events
become the same H.* calls), and the window behaves like the Mac one where it matters: it never takes focus or mouse
clicks, it is hidden from screenshots and screen capture (so the screen agent sees the real screen), and it only
appears while there is something to show (a small pill at the top while Studio is open, the full card at the bottom
while listening, thinking or speaking).

Global keys, polled with GetAsyncKeyState so there is no extra dependency: Esc (stop everything), Space (interrupt and
listen while Monday is busy) and right Ctrl held (push-to-talk; right Alt would open menus in other apps)."""
import ctypes
import json
import os
import threading
import time
from ctypes import wintypes
from pathlib import Path

from . import hud_native as core

HTML = Path(__file__).with_name("static") / "hud_win.html"
TITLE = "Monday HUD (Windows)"
SIZES = {"island": (112, 34), "full": (560, 148)}  # CSS pixels
MARGIN = 18  # above the taskbar
VK_ESC, VK_SPACE, VK_CTRL, VK_ALT, VK_RCTRL = 0x1B, 0x20, 0x11, 0x12, 0xA3
GWL_EXSTYLE = -20
WS_EX_LAYERED, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x80000, 0x20, 0x80, 0x8000000
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE, SWP_FRAMECHANGED = 0x1, 0x2, 0x4, 0x10, 0x20
HWND_TOPMOST = ctypes.c_void_p(-1)
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
WDA_NONE, WDA_EXCLUDEFROMCAPTURE = 0, 0x11
LWA_ALPHA = 0x2
SHOW_FOR = 3.5  # seconds the full card stays after the last caption or activity

_act = {"at": 0.0}  # when something last happened that deserves the full card
_win = None
_u32 = None
_keep: list = []  # callbacks must outlive the call that registers them


def _user32():
    global _u32
    if _u32 is None:
        u = ctypes.WinDLL("user32", use_last_error=True)
        u.FindWindowW.restype = wintypes.HWND
        u.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        u.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        u.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        u.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        u.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        u.SetWindowPos.argtypes = [wintypes.HWND, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_uint]
        u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        u.IsWindowVisible.argtypes = [wintypes.HWND]
        u.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.COLORREF, ctypes.c_ubyte, wintypes.DWORD]
        u.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
        u.GetDpiForWindow.restype = ctypes.c_uint
        u.GetDpiForWindow.argtypes = [wintypes.HWND]
        u.GetAsyncKeyState.argtypes = [ctypes.c_int]
        u.SystemParametersInfoW.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
        _u32 = u
    return _u32


def _find_hwnd(timeout: float = 20) -> int:
    u, end = _user32(), time.time() + timeout
    while time.time() < end:
        h = u.FindWindowW(None, TITLE)
        if h:
            return h
        time.sleep(0.1)
    return 0


def _style(hwnd: int):
    """Never takes focus, hidden from the taskbar and Alt+Tab, click-through, excluded from screen capture."""
    u = _user32()
    ex = u.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    u.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)
    u.SetLayeredWindowAttributes(hwnd, 0, 0, LWA_ALPHA)
    u.SetWindowPos(hwnd, None, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED)
    # FORGE_HUD_CAPTURE=1 lets screenshots see the HUD (for checking the design); otherwise the agent never does
    u.SetWindowDisplayAffinity(hwnd, WDA_NONE if os.getenv("FORGE_HUD_CAPTURE") == "1" else WDA_EXCLUDEFROMCAPTURE)
    try:  # Windows 11: rounded corners and no 1px frame
        pref = ctypes.c_int(2)  # DWMWCP_ROUND
        ctypes.windll.dwmapi.DwmSetWindowAttribute(wintypes.HWND(hwnd), 33, ctypes.byref(pref), 4)
    except Exception:
        pass


def _place(hwnd: int, layout: str):
    u = _user32()
    scale = (u.GetDpiForWindow(hwnd) or 96) / 96
    w, h = (round(v * scale) for v in SIZES[layout])
    area = wintypes.RECT()
    u.SystemParametersInfoW(0x30, 0, ctypes.byref(area), 0)  # SPI_GETWORKAREA: the screen minus the taskbar
    x = area.left + (area.right - area.left - w) // 2
    if layout == "island":  # the minimised state: a dynamic-island pill at the top centre
        y = area.top + round(6 * scale)
    else:  # listening, thinking or speaking: the full card, bottom centre above the taskbar
        y = area.bottom - h - round(MARGIN * scale)
    u.SetWindowPos(hwnd, HWND_TOPMOST, x, y, w, h, SWP_NOACTIVATE)


def _track(msg: dict):
    kind = msg.get("type")
    if kind in ("caption", "wake", "notice") or (kind == "state" and msg.get("state") != "idle"):
        _act["at"] = time.time()


def _js(call: str):
    try:
        _win.evaluate_js(call)
    except Exception:
        pass


def _loop(session):
    """Feed the page from the session's events and keep the window's size, fade and visibility right."""
    hwnd = _find_hwnd()
    if not hwnd:
        print("HUD window not found; the voice agent runs without its card.")
        return
    _win.events.loaded.wait(20)
    _style(hwnd)
    u = _user32()
    layout, alpha, shown, sent_level = "", 0.0, False, -1.0
    while True:
        time.sleep(1 / 30)
        with core._lock:
            parts, core._js[:] = core._js[:], []
        lv = round(core._st["level"], 2)
        if abs(lv - sent_level) >= 0.02:
            sent_level = lv
            parts.append(f"H.level({lv})")
        busy = core._st["mode"] != "idle" or core._st["talking"] or time.time() - _act["at"] < SHOW_FOR
        # the resting pill only hovers while Studio (the app or the website) is open; activity always shows the card
        want = "full" if busy else "island" if session.prefs.voice_mode and session.clients > 0 else ""
        if want and want != layout:
            layout = want
            _place(hwnd, layout)
            parts.append(f"H.layout({json.dumps(layout)})")
        if parts:
            _js("window.H&&(" + ",".join(parts) + ")")
        target = 255.0 if want else 0.0  # fade in and out instead of popping
        if alpha != target:
            alpha += (target - alpha) * 0.35
            if abs(target - alpha) < 4:
                alpha = target
            if alpha > 0 and not shown:
                _place(hwnd, layout or "island")
                u.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
                shown = True
            u.SetLayeredWindowAttributes(hwnd, 0, int(alpha), LWA_ALPHA)
            if alpha == 0 and shown:
                u.ShowWindow(hwnd, SW_HIDE)
                shown, layout = False, ""


def _keys(session):
    """Global hotkeys for the voice agent (same behaviour as the macOS key monitor)."""
    from ..runtime import self_typing
    u = _user32()

    def down(vk: int) -> bool:
        return bool(u.GetAsyncKeyState(vk) & 0x8000)

    def run(fn, *a):  # session calls can block (joining a task): keep this loop responsive
        threading.Thread(target=fn, args=a, daemon=True).start()
    held = space = esc = False
    while True:
        time.sleep(0.015)
        if self_typing():  # Monday's own keystrokes are not the user's
            continue
        ptt = down(VK_RCTRL)
        if ptt != held:
            held = ptt
            run(session.ptt_key, held)
        sp = down(VK_SPACE) and not down(VK_CTRL) and not down(VK_ALT)
        if sp and not space:
            run(session.space_tap)
        space = sp
        e = down(VK_ESC)
        if e and not esc:
            run(session.escape)
        esc = e


def _exit_on_ctrl_c(win):
    """The window loop is native code, so Python never sees Ctrl+C: catch it with a console handler instead."""
    handler_t = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

    def handler(event):
        try:
            win.destroy()
        except Exception:
            pass
        threading.Timer(3, lambda: os._exit(0)).start()
        return True
    _keep.append(handler_t(handler))
    ctypes.windll.kernel32.SetConsoleCtrlHandler(_keep[-1], True)


def window_args() -> dict:
    """The pywebview create_window() arguments for the HUD card."""
    w, h = SIZES["island"]
    return dict(title=TITLE, url=str(HTML), width=w, height=h, frameless=True, on_top=True, focus=False, shadow=False,
                easy_drag=False, resizable=False, hidden=True, min_size=(100, 40),
                background_color="#0e1118", text_select=False)


def attach(window, session):
    """Start feeding the HUD window and listening for the hotkeys. Call after webview.start() has begun."""
    global _win
    _win = window
    session.listeners.append(core.on_event)
    session.listeners.append(_track)
    session.hud_on = True
    core.state(session.state)
    threading.Thread(target=_loop, args=(session,), daemon=True).start()
    threading.Thread(target=_keys, args=(session,), daemon=True).start()


def run():
    """The HUD as its own app (`monday voice`): only this card, until Ctrl+C."""
    import webview
    session = core.wait_for_session()
    if session is None:
        raise RuntimeError("the Monday session did not start")
    win = webview.create_window(**window_args())
    _exit_on_ctrl_c(win)
    webview.start(lambda: attach(win, session))




