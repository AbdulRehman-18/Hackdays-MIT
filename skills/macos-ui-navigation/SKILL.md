---
name: macos-ui-navigation
description: Operate any macOS app through its real controls - buttons, text fields, menus and tabs - using the Accessibility element list instead of guessing pixels. Use for any multi-step task inside a Mac app such as Notes, Finder, Calendar, Music or Preview.
license: MIT
compatibility: macOS with Accessibility permission granted to the agent's host app
metadata:
  author: forge
  version: "1.0"
---

# Navigating macOS apps

## Order of preference
1. A direct tool, if one exists (`open_app`, `open_url`, `open_file`). One call beats five clicks.
2. A keyboard shortcut you are sure of (see [references/shortcuts.md](references/shortcuts.md)).
3. `click_element` / `type_into` with an id from the **current** element list.
4. `click` with screenshot coordinates, only when the target is not in the list (canvas, video, image).

## Steps
1. Check the front app in the observation. If the wrong app is in front, call `open_app` first.
2. Find the control in the element list by its role and label (for example `[7] search 'Search'`).
3. Act on it: `type_into` for fields (set `press_enter` when the field submits), `click_element` for the rest.
4. Read the next observation. If the screen did not change, do not repeat the same action:
   try the menu bar, a shortcut, or scrolling to reveal the control.
5. Call `done` with one short sentence as soon as the goal is visibly complete.

## Pitfalls
- Element ids change every step. Never reuse an id from an earlier observation.
- A sheet or dialog may be covering the window; handle it first (often `esc` or a "Cancel" button).
- Never click buttons labelled Delete, Send, Buy, Pay or Submit unless the user asked for exactly that.
