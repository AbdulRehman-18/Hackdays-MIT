"""Local route: any model served by Ollama. The JSON schema is enforced while decoding (`format`)."""
import base64
import json
import time
from collections.abc import Iterator

import httpx

from .base import BackendError, Completion, Delta, Message


class OllamaBackend:
    name = "local"

    def __init__(self, model: str, url: str = "http://127.0.0.1:11434", keep_alive: str = "10m",
                 timeout: float = 180.0, transport: httpx.BaseTransport | None = None, num_ctx: int = 8192):
        self.model, self.keep_alive, self.num_ctx = model, keep_alive, num_ctx
        self.http = httpx.Client(base_url=url, timeout=timeout, transport=transport)

    def payload(self, messages: list[Message], schema: dict | None, temperature: float) -> dict:
        msgs = []
        for m in messages:
            d = {"role": m.role, "content": m.content}
            if m.images:
                d["images"] = [base64.b64encode(i).decode() for i in m.images]
            msgs.append(d)
        body = {"model": self.model, "messages": msgs, "stream": False, "keep_alive": self.keep_alive,
                "options": {"temperature": temperature, "num_ctx": self.num_ctx}}
        if schema is not None:
            body["format"] = schema
            body["think"] = False  # decisions must be fast: thinking is only for the streamed answer
        return body

    def _post(self, body: dict) -> httpx.Response:
        """POST once more if Ollama drops the connection: it does when its runner restarts or reloads a model."""
        for attempt in range(2):
            try:
                return self.http.post("/api/chat", json=body)
            except httpx.ConnectError as e:
                raise BackendError(f"Ollama is not reachable at {self.http.base_url}. "
                                   "Start it with `ollama serve`.") from e
            except (httpx.RemoteProtocolError, httpx.ReadError) as e:
                if attempt:
                    raise BackendError(f"Ollama dropped the connection twice while running {self.model}. It may "
                                       "be out of memory: pick a smaller model or lower OLLAMA_NUM_CTX.") from e
        raise AssertionError("unreachable")

    def complete(self, messages: list[Message], schema: dict | None = None,
                 temperature: float = 0.2) -> Completion:
        t = time.perf_counter()
        r = self._post(self.payload(messages, schema, temperature))
        if r.status_code == 404:
            raise BackendError(f"Model {self.model!r} is not pulled. Run: ollama pull {self.model}")
        if r.status_code >= 400:
            raise BackendError(_explain(r.status_code, r.text, self.model))
        d = r.json()
        return Completion(text=d.get("message", {}).get("content", ""), seconds=time.perf_counter() - t,
                          prompt_tokens=d.get("prompt_eval_count", 0), output_tokens=d.get("eval_count", 0),
                          constrained=schema is not None)

    def native_tools(self, messages: list[Message], tools: list[dict], temperature: float = 0.2) -> tuple[dict, float]:
        """Ollama's own tool calling (no Forge schema, prompt or repair): the baseline Forge is compared with."""
        body = self.payload(messages, None, temperature)
        body["tools"] = tools
        t = time.perf_counter()
        r = self.http.post("/api/chat", json=body)
        if r.status_code >= 400:
            raise BackendError(f"Ollama error {r.status_code}: {r.text[:300]}")
        return r.json().get("message", {}), time.perf_counter() - t

    def stream(self, messages: list[Message], think: bool = False, temperature: float = 0.6) -> Iterator[Delta]:
        body = self.payload(messages, None, temperature)
        body["stream"] = True
        body["think"] = think
        t = time.perf_counter()
        try:
            with self.http.stream("POST", "/api/chat", json=body) as r:
                if r.status_code == 404:
                    raise BackendError(f"Model {self.model!r} is not pulled. Run: ollama pull {self.model}")
                if r.status_code >= 400:
                    r.read()
                    if think and "thinking" in r.text:  # this model cannot think: answer without it
                        yield from self.stream(messages, False, temperature)
                        return
                    raise BackendError(_explain(r.status_code, r.text, self.model))
                for line in r.iter_lines():
                    if not line.strip():
                        continue
                    d = json.loads(line)
                    if d.get("error"):
                        raise BackendError(d["error"])
                    m = d.get("message", {})
                    if m.get("thinking"):
                        yield Delta("thinking", m["thinking"])
                    if m.get("content"):
                        yield Delta("text", m["content"])
                    if d.get("done"):
                        yield Delta("done", prompt_tokens=d.get("prompt_eval_count", 0),
                                    output_tokens=d.get("eval_count", 0), seconds=time.perf_counter() - t)
        except httpx.ConnectError as e:
            raise BackendError(f"Ollama is not reachable at {self.http.base_url}. Start it with `ollama serve`.") from e
        except (httpx.RemoteProtocolError, httpx.ReadError) as e:
            raise BackendError(f"Ollama dropped the connection while answering with {self.model}. It may be out "
                               "of memory: pick a smaller model or lower OLLAMA_NUM_CTX.") from e


def _explain(status: int, text: str, model: str) -> str:
    if "memory" in text.lower():
        return (f"{model} does not fit in this computer's free memory. Pick a smaller model (Model hub), close "
                f"heavy apps, or lower OLLAMA_NUM_CTX. Ollama said: {text[:200]}")
    return f"Ollama error {status}: {text[:300]}"

