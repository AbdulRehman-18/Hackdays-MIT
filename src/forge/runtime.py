"""Shared runtime state: the model tools may consult (summaries, screen reading) and the event sink.

The assistant sets these at startup, so tools stay plain functions with no model plumbing of their own."""
import time
from collections.abc import Callable

from .harness.backends import Backend, Message

fast: Backend | None = None  # quick text and vision answers
emit: Callable[[str, dict], None] = lambda kind, data: None  # noqa: E731


def ask(prompt: str, images: list[bytes] | None = None, system: str = "") -> str:
    if fast is None:
        raise RuntimeError("no model configured")
    msgs = ([Message("system", system)] if system else []) + [Message("user", prompt, images or [])]
    return fast.complete(msgs, temperature=0.3).text.strip()


_self_keys = [0.0]  # when Forge last typed or pressed a key itself


def typed_by_me():
    """Mark that Forge just sent keys, so the HUD key monitor does not take its own Space or Esc as the user's."""
    _self_keys[0] = time.monotonic()


def self_typing() -> bool:
    return time.monotonic() - _self_keys[0] < 0.4
