"""Settings read from the environment (and a .env file in the working directory).

Model, context and speech defaults come from the hardware profile (see hardware.py); anything set in .env wins."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from .hardware import detect, profile

load_dotenv()
SKILLS_DIR = Path(os.getenv("FORGE_SKILLS_DIR", Path(__file__).resolve().parents[2] / "skills"))
# the voice agent is the owner's Monday program itself (its main.py, HUD, listening and voice), running on Gemma
MONDAY_DIR = Path(os.getenv("MONDAY_DIR", Path(__file__).resolve().parents[2] / "monday" / "monday"))


def monday_command() -> list[str] | None:
    """How to start the Monday voice agent, or None when its folder is missing."""
    if not (MONDAY_DIR / "main.py").is_file():
        return None
    py = MONDAY_DIR / ".venv" / "bin" / "python"
    import sys
    return [str(py if py.exists() else sys.executable), "main.py"]
HARDWARE = detect()
PROFILE = profile(HARDWARE)


@dataclass
class Settings:
    name: str = os.getenv("FORGE_NAME", "Monday")
    route: str = os.getenv("FORGE_ROUTE", "local")  # local (Ollama), openai (any OpenAI-compatible server), aistudio
    ollama_url: str = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
    fast_model: str = os.getenv("FORGE_FAST_MODEL", PROFILE.fast_model)
    agent_model: str = os.getenv("FORGE_AGENT_MODEL", PROFILE.agent_model)
    keep_alive: str = os.getenv("OLLAMA_KEEP_ALIVE", "30m")
    num_ctx: int = int(os.getenv("OLLAMA_NUM_CTX", str(PROFILE.num_ctx)))
    openai_url: str = os.getenv("FORGE_OPENAI_URL", "http://127.0.0.1:11434/v1")
    openai_model: str = os.getenv("FORGE_OPENAI_MODEL", PROFILE.fast_model)
    openai_key: str = os.getenv("FORGE_OPENAI_KEY", "")
    gemini_key: str = os.getenv("GEMINI_API_KEY", "")
    aistudio_model: str = os.getenv("FORGE_AISTUDIO_MODEL", "gemma-4-26b-a4b-it")
    max_steps: int = int(os.getenv("FORGE_MAX_STEPS", "12"))
    plan: bool = os.getenv("FORGE_PLAN", "1") != "0"  # split compound requests into a checklist first
    trace_dir: str = os.getenv("FORGE_TRACE_DIR", str(os.path.expanduser("~/.forge/traces")))
    host: str = os.getenv("FORGE_HOST", "127.0.0.1")
    port: int = int(os.getenv("FORGE_PORT", "8765"))


settings = Settings()
