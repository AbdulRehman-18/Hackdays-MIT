"""Eyes and hands beyond the screen: the webcam, and drawing pictures in Excalidraw with the mouse."""
import json
import time
from typing import Annotated

from pydantic import Field

from .. import runtime
from ..harness.backends import Message
from ..harness.tools import tool
from . import screen
from .mac import run


def snapshot(index: int = 0) -> bytes:
    import cv2
    cam = cv2.VideoCapture(index)
    try:
        if not cam.isOpened():
            raise RuntimeError("I can't open the camera: allow Camera access for this app in System Settings")
        frame = None
        for _ in range(12):  # let exposure settle
            ok, frame = cam.read()
            time.sleep(0.03)
        if frame is None:
            raise RuntimeError("the camera returned no picture")
        ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return jpg.tobytes()
    finally:
        cam.release()


@tool(group="senses")
def look(question: Annotated[str, Field(description="what the user wants to know")] = "What do you see?") -> str:
    """Look through the webcam: what the user is holding or showing, how they look, what is around them."""
    jpeg = snapshot()
    runtime.emit("image", {"jpeg": jpeg})
    return runtime.ask(f"This is a live webcam photo of the user. {question}\n"
                       "Describe what matters in 1 to 3 short, friendly sentences.", [jpeg])


SKETCH_SCHEMA = {"type": "object", "properties": {"strokes": {
    "type": "array", "minItems": 3, "maxItems": 24,
    "items": {"type": "array", "minItems": 2, "maxItems": 40,
              "items": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}}}},
    "required": ["strokes"]}
SKETCH = """Plan a simple, recognisable line drawing of: {subject}.
Use a 100 x 100 canvas, x to the right, y downwards. Return strokes: each stroke is a list of [x, y] points the pen
follows without lifting. Use smooth outlines with enough points for curves (a circle needs 16+ points), then add the
key details that make it recognisable. Keep everything between 5 and 95."""


@tool(group="senses")
def sketch(subject: Annotated[str, Field(description="what to draw, e.g. a cat, a rocket, a house")]) -> str:
    """Draw a picture with the mouse in Excalidraw (opens it in the browser)."""
    c = runtime.fast.complete([Message("user", SKETCH.format(subject=subject))], schema=SKETCH_SCHEMA,
                              temperature=0.4)
    strokes = json.loads(c.text)["strokes"]
    run("open", "https://excalidraw.com")
    time.sleep(4.5)
    w, h = screen.display_points()
    side = min(w, h) * 0.5
    x0, y0 = (w - side) / 2, (h - side) / 2 + h * 0.03
    screen.click_points(w * 0.5, h * 0.9)  # focus the canvas
    screen.press("p")  # Excalidraw's free-draw tool
    time.sleep(0.3)
    for st in strokes:
        pts = [(x0 + max(0, min(100, x)) / 100 * side, y0 + max(0, min(100, y)) / 100 * side) for x, y in st]
        screen.drag_path(pts)
    screen.press("v")
    return f"Drew {subject} in Excalidraw with {len(strokes)} strokes."


TOOLS = [look, sketch]
