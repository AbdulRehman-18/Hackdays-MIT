"""Images: remove a background on device with the open U²-Net model (rembg)."""
from pathlib import Path
from typing import Annotated

from pydantic import Field

from ..harness.tools import tool
from .files import resolve

IMAGES = {".png", ".jpg", ".jpeg", ".webp", ".heic"}


def newest_desktop_image() -> Path:
    imgs = [p for p in (Path.home() / "Desktop").iterdir() if p.suffix.lower() in IMAGES and "_nobg" not in p.stem]
    if not imgs:
        raise FileNotFoundError("there's no image on your Desktop")
    return max(imgs, key=lambda p: p.stat().st_mtime)


@tool(group="media")
def remove_background(path: Annotated[str, Field(description="image name or path; empty for the newest image on "
                                                             "the Desktop")] = "") -> str:
    """Cut the background out of an image and save a transparent PNG next to it."""
    try:
        from rembg import remove
    except ImportError:
        return "error: background removal needs the media extra: uv sync --extra media"
    from PIL import Image
    src = resolve(path) if path else newest_desktop_image()
    out = src.with_name(f"{src.stem}_nobg.png")
    remove(Image.open(src)).save(out)
    return f"Done! The cut-out is saved as {out.name}."


TOOLS = [remove_background]
