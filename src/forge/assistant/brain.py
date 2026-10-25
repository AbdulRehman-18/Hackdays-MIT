"""Monday's routing, fastest path first, in front of the harness (ported from monday/brain.py and router.py):

1. LOCAL    instant regex intents: time, date, battery, volume, media, timers, weather, calendar, screenshot,
            lock, HUD mode, portfolio. No model at all.
2. fast     fixed patterns, even multi-step ones: webcam questions, YouTube, drawing, open <file.ext>,
            read the screen, open an app, Google search. No model at all.
3. None     everything else goes to the Gemma harness (plan, constrained decisions, tools, screen agent).

Each handler returns the reply to speak, "" when it already spoke, or the route returns None for the harness."""
import datetime
import os
import re
import urllib.parse
from collections.abc import Callable

from ..tools import apple, files, senses, system, web
from ..tools.apps import open_app

PORTFOLIO_URL = os.getenv("FORGE_PORTFOLIO_URL", "https://abdulrehman-18.vercel.app")
Say = Callable[[str], None]


def _err(r: str) -> str:
    return r.replace("error: ", "") if r.startswith("error") else r


def _weather(t: str) -> str:
    m = re.search(r"\b(?:in|at|for)\s+([a-z][a-z .'-]+?)(?:\s+(?:today|tonight|now|right now|this week|tomorrow))?$",
                  t, re.I)
    place = m.group(1) if m and m.group(1).lower() not in ("today", "tonight", "now", "tomorrow") else ""
    return web.weather.fn(place)


def _calendar(t: str) -> str:
    try:
        return apple.calendar.fn(1 if re.search(r"\btomorrow\b", t, re.I) else 0)
    except Exception as e:
        if "1743" in str(e):
            return "I need permission to read your Calendar. Check System Settings, Privacy, Automation."
        return f"Hmm, the calendar didn't answer: {e}"


def _volume(level: int | None = None, delta: int = 0, mute: bool | None = None) -> str:
    if mute is True:
        system.volume.fn(-1)
        return "Muted."
    if mute is False:
        system.change_volume(0)
        return "Sound is back on."
    if level is not None:
        system.volume.fn(max(0, min(100, level)))
        return f"Volume {max(0, min(100, level))} percent."
    return system.change_volume(delta)


def _media(action: str) -> str:
    system.media.fn(action)
    return "Done."


class Router:
    """LOCAL intents and router patterns. say speaks an acknowledgement before slow work starts;
    set_hud switches the HUD between full screen and the Dynamic Island."""

    def __init__(self, say: Say, set_hud: Callable[[str], bool] | None = None):
        self.say, self.set_hud = say, set_hud or (lambda mode: False)
        self.local = [
            (r"\b(?:cancel|stop|delete|clear|remove)\b.*\btimers?\b", lambda m: apple.cancel_timers()),
            (r"\bhow (?:much|long)\b.*\btimer\b|\btimers?\b.*\bleft\b", lambda m: apple.timers_left()),
            (r"\btimer\b.*\d|\b\d+\s*(?:second|sec|minute|min|hour|hr)s?\s+timer\b|\btimer for (?:a|an|half)\b",
             lambda m: apple.set_timer.fn(apple.parse_duration(m.string))),
            (r"\bweather\b|\btemperature (?:outside|today|in)\b|\b(?:will|is) it (?:going to )?(?:rain|snow)\b"
             r"|\bforecast\b", lambda m: _weather(m.string)),
            (r"\b(?:take|grab|capture) (?:a )?screenshot\b|^screenshot$", lambda m: system.take_screenshot.fn()),
            (r"\block (?:my |the )?(?:mac|screen|computer|laptop)\b", lambda m: system.lock_screen.fn()),
            (r"^(?!.*\b(?:add|create|schedule|put|book|set up|make|move|cancel)\b).*(?:\bon my calendar\b"
             r"|\bmy (?:schedule|agenda|meetings?)\b)", lambda m: _calendar(m.string)),
            (r"\bportfolio\b", lambda m: self.portfolio()),
            (r"\bdynamic island\b|\b(?:island|notch|compact|small|mini) (?:mode|view)\b|\b(?:go|move|get) (?:to|in|"
             r"into|inside) (?:the |my )?(?:notch|island)\b|\bminimi[sz]e (?:yourself|forge|the hud)\b|^minimi[sz]e$",
             lambda m: self.hud("island")),
            (r"\bfull[ -]?(?:screen|window)? (?:view|mode)\b|\b(?:big|normal) mode\b|\bmaximi[sz]e (?:yourself|"
             r"forge|the hud)\b|^maximi[sz]e$|\bcome out of the notch\b", lambda m: self.hud("full")),
            (r"\bwhat(?:'s| is)?(?: the)? time\b|\btime is it\b|\bcurrent time\b|^time$",
             lambda m: datetime.datetime.now().strftime("It's %-I:%M %p.")),
            (r"\bwhat(?:'s| is)?(?: the| today'?s)? date\b|\bwhat day is (?:it|today)\b|\btoday'?s date\b",
             lambda m: datetime.datetime.now().strftime("It's %A, %B %-d.")),
            (r"\bbattery\b", lambda m: system.battery.fn()),
            (r"\b(?:set )?(?:the )?volume (?:to )?(\d{1,3})\b", lambda m: _volume(level=int(m.group(1)))),
            (r"\b(?:volume|sound) up\b|\blouder\b|\bincrease (?:the )?volume\b", lambda m: _volume(delta=15)),
            (r"\b(?:volume|sound) down\b|\bquieter\b|\b(?:decrease|lower) (?:the )?volume\b",
             lambda m: _volume(delta=-15)),
            (r"^(?:please )?unmute\b", lambda m: _volume(mute=False)),
            (r"^(?:please )?mute\b", lambda m: _volume(mute=True)),
            (r"^(?:please )?(?:pause|resume|play)(?: the)?(?: music| video| song)?$|^stop the (?:music|video|song)$",
             lambda m: _media("playpause")),
            (r"^(?:next|skip)(?: the)?(?: song| track| video)?$", lambda m: _media("next")),
            (r"^previous(?: song| track| video)?$", lambda m: _media("previous")),
        ]

    def portfolio(self) -> str:
        web.open_url.fn(PORTFOLIO_URL)
        return "Opening your portfolio! Looking good."

    def hud(self, mode: str) -> str:
        if not self.set_hud(mode):
            return "The HUD is turned off right now."
        return "Island mode! I'll live in the notch." if mode == "island" else "Full screen mode!"

    def youtube(self, q: str, play: bool) -> str:
        self.say(f"Playing {q}." if play else f"Searching YouTube for {q}.")
        web.youtube.fn(q, play)
        return ""

    def route(self, text: str) -> tuple[str, str] | None:
        """(path, reply) when LOCAL or a router pattern handled it, else None for the harness."""
        text = re.sub(r"(?i)\s+dot\s+(py|js|ts|tsx|jsx|md|txt|pdf|json|html|css|swift|java|cpp|go|rs|csv|png|jpg)\b",
                      r".\1", text)
        t = re.sub(r"(?i)^(?:hey |ok |okay )?forge[, ]+", "", text.strip()).rstrip(".!?")
        if COMPOUND.search(t):
            # several things at once ("weather in London and save it as a note"): an instant path would do only
            # the first, so the harness plans it. "open YouTube and play lofi" is still one deterministic step.
            m = YT[0].search(t)
            if m and not COMPOUND.search(m.group(2)):
                return "router", self.youtube(_clean(m.group(2)), m.group(1).lower() in ("play", "put on"))
            return None
        for rx, fn in self.local:
            m = re.search(rx, t, re.I)
            if m:
                return "instant", fn(m)
        reply = self.fast(t)
        return ("router", reply) if reply is not None else None

    def fast(self, t: str) -> str | None:
        """Known commands without any model, even multi-step ones (monday/router.py)."""
        if LOOK.search(t):
            self.say("Let me look.")
            return senses.look.fn(t)
        for rx in YT:
            m = rx.search(t)
            if m:
                return self.youtube(_clean(m.group(2)), m.group(1).lower() in ("play", "put on"))
        m = DRAW.search(t)
        if m and not re.search(r"\b(j?s?paint)\b", t, re.I):
            subject = _clean(m.group(1))
            self.say(f"Drawing {subject}.")
            r = senses.sketch.fn(subject)
            return _err(r) if r.startswith("error") else f"Here's your {subject}."
        m = re.search(r"(?i)\bopen (?:up )?(?:the |my |a )?([\w\-./~]+\.[a-z0-9]{1,6})\b(?: file)?", t)
        if m:
            try:
                r = files.open_file.fn(m.group(1))
            except FileNotFoundError as e:
                return f"Hmm, {e}."
            return _err(r) if r.startswith("error") else f"Opened {m.group(1)}!"
        if re.search(r"(?i)\b(read|explain) (?:this|that|the) (?:code|page|error|screen|text)\b"
                     r"|\bwhat(?:'s| is) on (?:my|the) screen\b", t):
            self.say("Let me see.")
            return system.read_screen.fn(t)
        m = re.match(r"(?i)^(?:please\s+)?(?:open|launch|start)\s+(?:the\s+)?(.+)$", t)
        if m and len(m.group(1).split()) <= 3 and not NOT_APP.search(m.group(1)):
            r = open_app.fn(m.group(1))
            return _err(r) if r.startswith("error") else f"Opened {m.group(1)}."
        m = re.match(r"(?i)^(?:please\s+)?(?:search|google)(?:\s+for)?\s+(.+)$", t)
        if m:
            self.say(f"Searching for {m.group(1)}.")
            web.open_url.fn("https://www.google.com/search?q=" + urllib.parse.quote_plus(m.group(1)))
            return ""
        return None


COMPOUND = re.compile(r"\b(?:and|then|also|after that)\b", re.I)
NOT_APP = re.compile(r"\b(file|pdf|image|photo|document|folder|my|about)\b", re.I)
LOOK = re.compile(r"\b(what am i holding|what(?:'s| is) (?:this|that|in my hand)|what do you see|look at (?:me|this)|"
                  r"use (?:the |my )?camera|can you see (?:me|this)|how do i look|identify this)\b", re.I)
YT = [re.compile(r"(?i)\byoutube\b.*?\b(play|search|find|look up|put on)\b(?:\s+for)?\s+(.+)$"),
      re.compile(r"(?i)\b(play|search|find|put on)\b(?:\s+for)?\s+(.+?)\s+(?:on|in)\s+youtube\b"),
      re.compile(r"(?i)^(?:please\s+)?(play|put on)\s+(.+)$")]
DRAW = re.compile(r"(?i)\bdraw(?:\s+me)?\s+(?:a|an|the|some)?\s*(.+)$")


def _clean(q: str) -> str:
    return re.sub(r"(?i)^(?:any|some|a|an|the)\s+|\s+(?:on|in)\s+youtube$", "", q.strip()).strip() or q
