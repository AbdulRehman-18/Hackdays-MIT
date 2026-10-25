"""What this computer can run, and the defaults that follow from it.

On Apple Silicon the model shares memory with everything else, and macOS lets the GPU use roughly 70% of it
(Ollama reports 11.8 GB usable on a 16 GB M4). Defaults are picked so the chat model, the speech model and the
apps you have open fit at once; a model that does not fit spills onto the CPU or gets swapped in and out,
which is what makes a small model feel slow or crash. Every default can still be overridden in .env."""
import os
import platform
import subprocess
from dataclasses import dataclass


@dataclass
class Hardware:
    chip: str
    ram_gb: float
    apple_silicon: bool

    @property
    def gpu_budget_gb(self) -> float:
        """Memory a model can use on the GPU: about 70% of unified memory, nothing on machines without Metal."""
        return round(self.ram_gb * 0.7, 1) if self.apple_silicon else 0.0

    @property
    def tier(self) -> str:
        if not self.apple_silicon:
            return "cpu"
        return "small" if self.ram_gb < 12 else "medium" if self.ram_gb < 24 else "large" if self.ram_gb < 48 else "xl"


@dataclass
class Profile:
    fast_model: str   # every chat and voice request
    agent_model: str  # the screen agent; the same model as fast_model when two would not fit in memory together
    num_ctx: int
    stt_model: str
    note: str


PROFILES = {
    "cpu": Profile("gemma4:e2b", "gemma4:e2b", 8192, "small.en",
                   "No Apple GPU: the smallest Gemma on the CPU. Expect a few seconds per step."),
    "small": Profile("gemma4:e2b", "gemma4:e2b", 8192, "mlx-community/whisper-small.en-mlx",
                     "8 GB: Gemma 4 E2B with a short context."),
    "medium": Profile("gemma4:e4b", "gemma4:e4b", 16384, "mlx-community/whisper-small.en-mlx",
                      "16 GB: Gemma 4 E4B for everything (a second, bigger model would not fit next to it)."),
    "large": Profile("gemma4:e4b", "gemma4:e4b", 32768, "mlx-community/whisper-small.en-mlx",
                     "24-36 GB: E4B with a long context. 12B QAT is an optional, smarter pick in the Model hub."),
    "xl": Profile("gemma4:e4b", "gemma4:e4b", 32768, "mlx-community/whisper-small.en-mlx",
                  "48 GB+: E4B with a long context. 12B QAT or 26B A4B are optional, smarter picks in the Model hub."),
}


def _sysctl(name: str) -> str:
    try:
        return subprocess.run(["sysctl", "-n", name], capture_output=True, text=True, timeout=2).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def detect() -> Hardware:
    system, machine = platform.system(), platform.machine()
    ram = 0.0
    if system == "Darwin":
        ram = int(_sysctl("hw.memsize") or 0) / 2**30
        chip = _sysctl("machdep.cpu.brand_string") or machine
    else:
        try:
            ram = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30
        except (ValueError, OSError, AttributeError):
            ram = 0.0
        chip = platform.processor() or machine
    return Hardware(chip, round(ram, 1), system == "Darwin" and machine == "arm64")


def profile(hw: Hardware | None = None) -> Profile:
    return PROFILES[(hw or detect()).tier]


def fits(size_gb: float, hw: Hardware | None = None) -> bool:
    """Whether a model of this size (as Ollama loads it) fits on the GPU with room left for speech and apps."""
    hw = hw or detect()
    return hw.apple_silicon and size_gb <= hw.gpu_budget_gb - 1.0
