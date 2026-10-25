"""AI Studio route: Gemma models hosted by Google, called through the google-genai SDK.

Hosted Gemma may refuse JSON mode or system instructions. Each refused feature is remembered per model and
replaced by plain prompting, so the harness's validate-and-repair step keeps the output usable either way."""
import json
import time
from collections.abc import Iterator

from .base import BackendError, Completion, Delta, Message


class AIStudioBackend:
    name = "aistudio"

    def __init__(self, model: str, api_key: str, client=None):
        if not api_key and client is None:
            raise BackendError("Set GEMINI_API_KEY in .env to use the aistudio route "
                               "(free key: https://aistudio.google.com/apikey).")
        if client is None:
            from google import genai
            client = genai.Client(api_key=api_key)
        self.model, self.client = model, client
        self.no_schema = self.no_system = False

    def _request(self, messages: list[Message], schema: dict | None, temperature: float):
        from google.genai import types
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        if schema is not None and self.no_schema:
            system += ("\n\nReply with JSON only, no prose and no code fences. It must match this JSON schema:\n"
                       + json.dumps(schema))
        contents = []
        for i, m in enumerate(m for m in messages if m.role != "system"):
            text = m.content
            if i == 0 and system and self.no_system:
                text = f"{system}\n\n{text}"
            parts = [types.Part.from_bytes(data=img, mime_type="image/jpeg") for img in m.images]
            parts.append(types.Part.from_text(text=text))
            contents.append(types.Content(role="model" if m.role == "assistant" else "user", parts=parts))
        cfg = {"temperature": temperature}
        if system and not self.no_system:
            cfg["system_instruction"] = system
        if schema is not None and not self.no_schema:
            cfg["response_mime_type"] = "application/json"
            cfg["response_json_schema"] = schema
        return contents, types.GenerateContentConfig(**cfg)

    def complete(self, messages: list[Message], schema: dict | None = None,
                 temperature: float = 0.2) -> Completion:
        for _ in range(3):  # at most two features can be dropped
            contents, cfg = self._request(messages, schema, temperature)
            t = time.perf_counter()
            try:
                r = self.client.models.generate_content(model=self.model, contents=contents, config=cfg)
            except Exception as e:
                msg = str(e).lower()
                if schema is not None and not self.no_schema and ("json" in msg or "schema" in msg):
                    self.no_schema = True
                    continue
                if not self.no_system and ("system" in msg or "developer instruction" in msg):
                    self.no_system = True
                    continue
                raise BackendError(f"AI Studio error: {e}") from e
            u = getattr(r, "usage_metadata", None)
            return Completion(text=r.text or "", seconds=time.perf_counter() - t,
                              prompt_tokens=getattr(u, "prompt_token_count", 0) or 0,
                              output_tokens=getattr(u, "candidates_token_count", 0) or 0,
                              constrained=schema is not None and not self.no_schema)
        raise BackendError("AI Studio rejected the request even without JSON mode and system instructions.")

    def stream(self, messages: list[Message], think: bool = False, temperature: float = 0.6) -> Iterator[Delta]:
        contents, cfg = self._request(messages, None, temperature)
        t, out = time.perf_counter(), 0
        try:
            for chunk in self.client.models.generate_content_stream(model=self.model, contents=contents, config=cfg):
                if chunk.text:
                    out += 1
                    yield Delta("text", chunk.text)
        except Exception as e:
            if not self.no_system and ("system" in str(e).lower() or "developer instruction" in str(e).lower()):
                self.no_system = True
                yield from self.stream(messages, think, temperature)
                return
            raise BackendError(f"AI Studio error: {e}") from e
        yield Delta("done", output_tokens=out, seconds=time.perf_counter() - t)

