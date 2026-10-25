import json
from types import SimpleNamespace

import httpx
import pytest

from forge.harness.backends import BackendError, Message
from forge.harness.backends.aistudio import AIStudioBackend
from forge.harness.backends.ollama import OllamaBackend


def test_ollama_sends_schema_images_and_reads_stats():
    seen = {}

    def handler(req):
        seen.update(json.loads(req.content))
        return httpx.Response(200, json={"message": {"content": "{}"}, "eval_count": 20, "prompt_eval_count": 50})
    b = OllamaBackend("gemma4:e4b", transport=httpx.MockTransport(handler))
    c = b.complete([Message("system", "s"), Message("user", "u", [b"\xff\xd8"])], schema={"type": "object"})
    assert seen["format"] == {"type": "object"} and seen["stream"] is False
    assert seen["messages"][1]["images"] == ["/9g="]
    assert c.output_tokens == 20 and c.constrained


def test_ollama_missing_model_gives_pull_hint():
    b = OllamaBackend("nope", transport=httpx.MockTransport(lambda r: httpx.Response(404, text="not found")))
    with pytest.raises(BackendError, match="ollama pull nope"):
        b.complete([Message("user", "hi")])


class FakeModels:
    def __init__(self, reject):
        self.reject, self.configs = list(reject), []

    def generate_content(self, model, contents, config):
        self.configs.append((contents, config))
        if self.reject:
            raise RuntimeError(self.reject.pop(0))
        return SimpleNamespace(text='{"ok": 1}', usage_metadata=SimpleNamespace(prompt_token_count=3,
                                                                                candidates_token_count=4))


def test_aistudio_uses_json_schema_when_accepted():
    m = FakeModels([])
    c = AIStudioBackend("gemma-4-26b-a4b-it", "", client=SimpleNamespace(models=m)).complete(
        [Message("system", "be brief"), Message("user", "hi")], schema={"type": "object"})
    cfg = m.configs[0][1]
    assert cfg.response_json_schema == {"type": "object"} and cfg.system_instruction == "be brief"
    assert c.constrained and c.output_tokens == 4


def test_aistudio_falls_back_when_json_mode_and_system_are_refused():
    m = FakeModels(["JSON mode is not enabled for this model", "Developer instruction is not enabled"])
    b = AIStudioBackend("gemma-4-26b-a4b-it", "", client=SimpleNamespace(models=m))
    c = b.complete([Message("system", "be brief"), Message("user", "hi")], schema={"type": "object"})
    contents, cfg = m.configs[-1]
    assert cfg.response_json_schema is None and cfg.system_instruction is None
    prompt = contents[0].parts[-1].text
    assert prompt.startswith("be brief") and "JSON schema" in prompt and not c.constrained
    assert b.no_schema and b.no_system  # remembered, so later calls skip the failing attempts


def test_aistudio_needs_a_key():
    with pytest.raises(BackendError, match="GEMINI_API_KEY"):
        AIStudioBackend("gemma-4-26b-a4b-it", "")


def test_openai_compatible_sends_json_schema_and_images():
    from forge.harness.backends.openai import OpenAIBackend
    seen = {}

    def handler(req):
        seen.update(json.loads(req.content))
        seen["url"] = str(req.url)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}],
                                         "usage": {"prompt_tokens": 50, "completion_tokens": 7}})
    b = OpenAIBackend("gemma-4-e4b", "http://127.0.0.1:8080/v1", transport=httpx.MockTransport(handler))
    c = b.complete([Message("system", "s"), Message("user", "u", [b"\xff\xd8"])], schema={"type": "object"})
    assert seen["url"] == "http://127.0.0.1:8080/v1/chat/completions"
    assert seen["response_format"]["json_schema"]["schema"] == {"type": "object"}
    assert seen["messages"][1]["content"][0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert c.output_tokens == 7 and c.constrained


def test_openai_compatible_falls_back_when_server_rejects_json_schema():
    from forge.harness.backends.openai import OpenAIBackend
    bodies = []

    def handler(req):
        body = json.loads(req.content)
        bodies.append(body)
        if body.get("response_format"):
            return httpx.Response(400, text="response_format is not supported")
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"a": 1}'}}]})
    b = OpenAIBackend("m", transport=httpx.MockTransport(handler))
    c = b.complete([Message("system", "s"), Message("user", "u")], schema={"type": "object"})
    assert c.text == '{"a": 1}' and not c.constrained
    assert "JSON schema" in bodies[-1]["messages"][0]["content"]  # the schema moved into the prompt
    b.complete([Message("user", "again")], schema={"type": "object"})
    assert len(bodies) == 4  # the refusal is remembered: no retries the second time


def test_openai_compatible_streams_text_thinking_and_usage():
    from forge.harness.backends.openai import OpenAIBackend
    sse = "\n".join([
        'data: {"choices": [{"delta": {"reasoning_content": "hmm"}}]}',
        'data: {"choices": [{"delta": {"content": "Hel"}}]}',
        'data: {"choices": [{"delta": {"content": "lo"}}]}',
        'data: {"choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 2}}',
        "data: [DONE]", ""])
    b = OpenAIBackend("m", transport=httpx.MockTransport(lambda r: httpx.Response(200, text=sse)))
    out = list(b.stream([Message("user", "hi")]))
    assert [d.text for d in out if d.kind == "text"] == ["Hel", "lo"]
    assert out[0].kind == "thinking" and out[-1].kind == "done" and out[-1].output_tokens == 2


def test_ollama_retries_once_when_the_connection_drops():
    n = {"calls": 0}

    def handler(req):
        n["calls"] += 1
        if n["calls"] == 1:
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
        return httpx.Response(200, json={"message": {"content": "hi"}})
    b = OllamaBackend("gemma4:e4b", transport=httpx.MockTransport(handler))
    assert b.complete([Message("user", "hi")]).text == "hi" and n["calls"] == 2


def test_ollama_out_of_memory_explains_the_fix():
    b = OllamaBackend("gemma4:26b", transport=httpx.MockTransport(
        lambda r: httpx.Response(500, text='{"error":"model requires more system memory (18 GiB)"}')))
    with pytest.raises(BackendError, match="smaller model"):
        b.complete([Message("user", "hi")])


def test_hardware_profiles_fit_in_memory():
    from forge.hardware import PROFILES, Hardware, fits, profile
    assert profile(Hardware("M1", 8, True)).fast_model == "gemma4:e2b"
    assert profile(Hardware("M4", 16, True)).agent_model == "gemma4:e4b"  # no second model on 16 GB
    assert profile(Hardware("M3 Max", 36, True)).agent_model == "gemma4:e4b"  # light by default
    assert "small.en" in profile(Hardware("M4", 16, True)).stt_model  # fast Whisper everywhere
    assert profile(Hardware("Intel", 16, False)) is PROFILES["cpu"]
    assert fits(9.5, Hardware("M4", 16, True)) and not fits(16, Hardware("M4", 16, True))
