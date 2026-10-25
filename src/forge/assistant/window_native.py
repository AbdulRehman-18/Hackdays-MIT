"""The Studio window's macOS chrome, in the style of the Codex and ChatGPT desktop apps.

The title bar is transparent and the page runs under it, so the traffic lights sit inside the sidebar; behind the
page is the system's own frosted sidebar material, which shows wherever the page is transparent (the sidebar).
The page knows it is in this window from ?native=1 and switches its sidebar to transparent. Every step fails soft:
if AppKit refuses anything, the window simply keeps the standard look."""
HEAD = 52.0  # height of the page's header row; the traffic lights are centred in it
LIGHTS_X, LIGHTS_GAP = 18.0, 20.0
FULL_SIZE_CONTENT, TITLE_HIDDEN = 1 << 15, 1
SIDEBAR_MATERIAL, BEHIND_WINDOW, ACTIVE, FILL = 7, 0, 1, 2 | 16  # NSViewWidthSizable | NSViewHeightSizable
_keep: list = []


def _clear(view):
    """pywebview paints the title bar with the window colour; clear it so the page shows through."""
    from AppKit import NSColor
    for v in (view, *view.subviews()):
        if v.respondsToSelector_("setBackgroundColor:"):
            v.setBackgroundColor_(NSColor.clearColor())
        if v.respondsToSelector_("setDrawsBackground:"):
            v.setDrawsBackground_(False)


def _place_lights(ns):
    """Centre the close / minimise / zoom buttons in the header row (macOS puts them back on every resize)."""
    buttons = [ns.standardWindowButton_(i) for i in (0, 1, 2)]
    if not all(buttons):
        return
    bar = buttons[0].superview().superview()  # the title bar container
    _clear(bar)
    frame = bar.frame()
    frame.size.height = HEAD
    frame.origin.y = ns.frame().size.height - HEAD
    bar.setFrame_(frame)
    for i, b in enumerate(buttons):
        r = b.frame()
        b.setFrameOrigin_((LIGHTS_X + i * LIGHTS_GAP, (HEAD - r.size.height) / 2))


def style(window):
    """Apply the look to a pywebview window once its page has loaded. Must run on the main thread."""
    try:
        from AppKit import NSNotificationCenter, NSView, NSVisualEffectView
        from Foundation import NSObject
        ns = window.native
        web = ns.contentView()
        if web is None or getattr(ns, "_monday_styled", False):
            return
        ns.setStyleMask_(ns.styleMask() | FULL_SIZE_CONTENT)
        ns.setTitlebarAppearsTransparent_(True)
        ns.setTitleVisibility_(TITLE_HIDDEN)
        web.setValue_forKey_(False, "drawsBackground")  # let the material show where the page is transparent

        container = NSView.alloc().initWithFrame_(web.frame())
        effect = NSVisualEffectView.alloc().initWithFrame_(container.bounds())
        effect.setMaterial_(SIDEBAR_MATERIAL)
        effect.setBlendingMode_(BEHIND_WINDOW)
        effect.setState_(ACTIVE)
        effect.setAutoresizingMask_(FILL)
        ns.setContentView_(container)
        container.addSubview_(effect)
        web.setFrame_(container.bounds())
        web.setAutoresizingMask_(FILL)
        container.addSubview_(web)
        ns.makeFirstResponder_(web)

        class Relight(NSObject):
            def relight_(self, _note):
                try:
                    _place_lights(ns)
                except Exception:
                    pass
        observer = Relight.alloc().init()
        center = NSNotificationCenter.defaultCenter()
        for name in ("NSWindowDidResizeNotification", "NSWindowDidEndLiveResizeNotification",
                     "NSWindowDidExitFullScreenNotification", "NSWindowDidBecomeKeyNotification"):
            center.addObserver_selector_name_object_(observer, "relight:", name, ns)
        _place_lights(ns)
        _keep.extend([container, effect, observer])
    except Exception as e:  # an AppKit change refused: keep the standard window
        print("window style skipped:", e)
