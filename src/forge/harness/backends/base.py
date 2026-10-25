"""The one interface every model route implements."""
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class Message:
    role: str  # system, user or assistant
    content: str
    images: list[bytes] = field(default_factory=list)  # JPEG/PNG bytes, user messages only


@dataclass
class Completion:
    text: str
    seconds: float
    prompt_tokens: int = 0
    output_tokens: int = 0
    constrained: bool = False  # True when the backend enforced the JSON schema while decoding

    @property
    def tokens_per_second(self) -> float:
        return self.output_tokens / self.seconds if self.seconds and self.output_tokens else 0.0


@dataclass
class Delta:
    """One piece of a streamed reply. kind is text, thinking or done (done carries the usage numbers)."""
    kind: str
    text: str = ""
    prompt_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0


class BackendError(RuntimeError):
    pass


class Backend(Protocol):
    name: str
    model: str

    def complete(self, messages: list[Message], schema: dict | None = None,
                 temperature: float = 0.2) -> Completion:
        """Generate one reply. With a schema, the reply must be JSON matching it."""
        ...

    def stream(self, messages: list[Message], think: bool = False, temperature: float = 0.6) -> Iterator[Delta]:
        """Stream a free-text reply, token by token."""
        ...
