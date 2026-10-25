"""OpenAI-compatible route: any local server that speaks /v1/chat/completions, set by URL and model name.

    Ollama     http://127.0.0.1:11434/v1        llama.cpp  http://127.0.0.1:8080/v1   (llama-server)
    LM Studio  http://127.0.0.1:1234/v1         MLX        http://127.0.0.1:8080/v1   (mlx_lm.server)

The JSON schema is sent as response_format json_schema, which Ollama, llama.cpp and LM Studio enforce while
decoding. A server that rejects it is asked for plain JSON mode, and then for nothing at all with the schema
written into the prompt; each refusal is remembered, and the harness's validate-and-repair keeps the output usable."""
import base64
import json
import time
from collections.abc import Iterator

import httpx

from .base import BackendError, Completion, Delta, Message

FORMATS = ("json_schema", "json_object", "none")  # tried in this order until the server accepts one


class OpenAIBackend:
    name = "openai"

    def __init__(self, model: str, base_url: str = "http://127.0.0.1:11434/v1", api_key: str = "",
                 timeout: float = 180.0, transport: httpx.BaseTransport | None = None):
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.model, self.base_url = model, base_url.rstrip("/")
        self.http = httpx.Client(base_url=self.base_url, timeout=timeout, headers=headers, transport=transport)
        self.format = 0  # index into FORMATS

    def _messages(self, messages: list[Message], schema: dict | None) -> list[dict]:
        out = []
        for m in messages:
            text = m.content
            if m.role == "system" and schema is not None and FORMATS[self.format] != "json_schema":
                text += "\n\nReply with JSON only, no prose and no code fences. It must match this JSON schema:\n" \
                        + json.dumps(schema)
            if m.images:
                parts = [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,"
                                                             + base64.b64encode(i).decode()}} for i in m.images]
                out.append({"role": m.role, "content": [*parts, {"type": "text", "text": text}]})
            else:
                out.append({"role": m.role, "content": text})
        return out

    def payload(self, messages: list[Message], schema: dict | None, temperature: float) -> dict:
        body = {"model": self.model, "messages": self._messages(messages, schema), "temperature": temperature,
                "stream": False}
        fmt = FORMATS[self.format]
        if schema is not None and fmt == "json_schema":
            body["response_format"] = {"type": "json_schema", "json_schema": {"name": "reply", "schema": schema}}
        elif schema is not None and fmt == "json_object":
            body["response_format"] = {"type": "json_object"}
        return body

    def _post(self, body: dict) -> httpx.Response:
        for attempt in range(2):
            try:
                return self.http.post("/chat/completions", json=body)
            except httpx.ConnectError as e:
                raise BackendError(f"No model server at {self.base_url}. Start Ollama, llama-server, LM Studio "
                                   "or mlx_lm.server, or set FORGE_OPENAI_URL.") from e
            except (httpx.RemoteProtocolError, httpx.ReadError):
                if attempt:  # the server dropped the connection twice (often: it ran out of memory)
                    raise BackendError(f"The server at {self.base_url} dropped the connection. It may be out "
                                       "of memory: try a smaller model or a shorter context.") from None
        raise AssertionError("unreachable")

    def complete(self, messages: list[Message], schema: dict | None = None,
                 temperature: float = 0.2) -> Completion:
        while True:
            t = time.perf_counter()
            r = self._post(self.payload(messages, schema, temperature))
            if r.status_code in (400, 422, 500, 501) and schema is not None and self.format < len(FORMATS) - 1 \
                    and any(w in r.text.lower() for w in ("response_format", "json", "schema", "grammar")):
                self.format += 1  # this server cannot do that format: remember, and ask for the next one
                continue
            if r.status_code == 404:
                raise BackendError(f"Model {self.model!r} was not found at {self.base_url}: {r.text[:200]}")
            if r.status_code >= 400:
                raise BackendError(f"Model server error {r.status_code}: {r.text[:300]}")
            d = r.json()
            u = d.get("usage") or {}
            msg = (d.get("choices") or [{}])[0].get("message") or {}
            return Completion(text=msg.get("content") or "", seconds=time.perf_counter() - t,
                              prompt_tokens=u.get("prompt_tokens", 0), output_tokens=u.get("completion_tokens", 0),
                              constrained=schema is not None and FORMATS[self.format] == "json_schema")

    def stream(self, messages: list[Message], think: bool = False, temperature: float = 0.6) -> Iterator[Delta]:
        body = self.payload(messages, None, temperature)
        body["stream"] = True
        body["stream_options"] = {"include_usage": True}
        t, usage = time.perf_counter(), {}
        try:
            with self.http.stream("POST", "/chat/completions", json=body) as r:
                if r.status_code >= 400:
                    r.read()
                    if "stream_options" in r.text:  # older servers: stream without the usage numbers
                        body.pop("stream_options")
                        yield from self._stream_body(body, t)
                        return
                    raise BackendError(f"Model server error {r.status_code}: {r.text[:300]}")
                yield from self._events(r, usage)
        except httpx.ConnectError as e:
            raise BackendError(f"No model server at {self.base_url}.") from e
        yield Delta("done", prompt_tokens=usage.get("prompt_tokens", 0),
                    output_tokens=usage.get("completion_tokens", 0), seconds=time.perf_counter() - t)

    def _stream_body(self, body: dict, t: float) -> Iterator[Delta]:
        usage: dict = {}
        with self.http.stream("POST", "/chat/completions", json=body) as r:
            if r.status_code >= 400:
                r.read()
                raise BackendError(f"Model server error {r.status_code}: {r.text[:300]}")
            yield from self._events(r, usage)
        yield Delta("done", seconds=time.perf_counter() - t)

    @staticmethod
    def _events(r: httpx.Response, usage: dict) -> Iterator[Delta]:
        for line in r.iter_lines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            d = json.loads(data)
            if d.get("error"):
                raise BackendError(str(d["error"]))
            usage.update(d.get("usage") or {})
            for ch in d.get("choices") or []:
                delta = ch.get("delta") or {}
                thought = delta.get("reasoning_content") or delta.get("reasoning")  # llama.cpp / LM Studio, Ollama
                if thought:
                    yield Delta("thinking", thought)
                if delta.get("content"):
                    yield Delta("text", delta["content"])

    def list_models(self) -> list[str]:
        r = self.http.get("/models", timeout=4)
        r.raise_for_status()
        return [m["id"] for m in r.json().get("data", [])]
