from .base import Backend, BackendError, Completion, Delta, Message


def make_backend(route: str, model: str | None = None, settings=None) -> Backend:
    """Build the backend for a route: local (Ollama), openai (any OpenAI-compatible server) or aistudio."""
    from ...config import settings as default
    s = settings or default
    if route == "local":
        from .ollama import OllamaBackend
        return OllamaBackend(model or s.agent_model, s.ollama_url, s.keep_alive, num_ctx=s.num_ctx)
    if route == "openai":
        from .openai import OpenAIBackend
        return OpenAIBackend(model or s.openai_model, s.openai_url, s.openai_key)
    if route == "aistudio":
        from .aistudio import AIStudioBackend
        return AIStudioBackend(model or s.aistudio_model, s.gemini_key)
    raise BackendError(f"Unknown route {route!r}: use local, openai or aistudio.")


__all__ = ["Backend", "BackendError", "Completion", "Delta", "Message", "make_backend"]
