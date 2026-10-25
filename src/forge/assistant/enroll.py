"""Voice lock setup (monday enroll).
  monday enroll          record five phrases and save your voiceprint
  monday enroll --check  test whether Monday recognises you in this room
  monday enroll --add    add clips from this (noisy) room to your voiceprint"""
import sys
import traceback

import numpy as np

from . import voiceprint
from .voice import VOICE_LOCK

SR = 16000
SECONDS = 4
PHRASES = ["Hey Monday", "Open the browser and search for Accelerator", "Find the PDF about my project",
           "Remove the background from the image on my desktop", "Monday, draw a rocket"]
CHECK_PHRASES = ["What's the time?", "Play some music on YouTube", "Stop"]


def check_setup():
    """Print what is installed and which microphone will be used."""
    import sounddevice as sd
    print("Voice lock setup:")
    print(f"  sounddevice {sd.__version__}")
    try:
        import librosa
        import resemblyzer
        import torch
        print(f"  torch {torch.__version__} · librosa {librosa.__version__} · "
              f"resemblyzer {getattr(resemblyzer, '__version__', '?')}")
    except Exception as e:
        sys.exit(f"  resemblyzer is not ready ({e}). Run:\n    uv sync --inexact --extra voicelock")
    try:
        d = sd.query_devices(kind="input")
        print(f"  microphone: {d['name']} · {int(d['default_samplerate'])} Hz · {d['max_input_channels']} channel(s)")
    except Exception as e:
        sys.exit(f"  No microphone available: {e}")
    print(f"  voiceprint file: {voiceprint.PATH} (exists: {voiceprint.PATH.exists()})")
    print(f"  FORGE_VOICE_LOCK in .env: {VOICE_LOCK}")


def record() -> np.ndarray:
    """Record SECONDS of mono 16 kHz audio, showing a live level bar."""
    import sounddevice as sd
    blocks, state = [], {"peak": 0.0, "level": 0.0, "status": ""}

    def cb(indata, frames, t, status):
        if status:
            state["status"] = str(status)
        block = indata[:, 0].copy()
        blocks.append(block)
        state["level"] = float(np.max(np.abs(block))) if len(block) else 0.0
        state["peak"] = max(state["peak"], state["level"])

    with sd.InputStream(samplerate=SR, channels=1, dtype="float32", blocksize=SR // 10, callback=cb):
        for _ in range(SECONDS * 10):
            sd.sleep(100)
            bar = "#" * int(min(1.0, state["level"] * 8) * 30)
            print(f"\r  level |{bar:<30}| peak {state['peak']:.3f}", end="", flush=True)
    print()
    if state["status"]:
        print("  device status:", state["status"])
    return np.concatenate(blocks) if blocks else np.zeros(0, "float32")


def report(audio: np.ndarray) -> bool:
    """Print how loud the clip was. Returns False when it caught almost nothing."""
    peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
    rms = float(np.sqrt(np.mean(audio ** 2))) if len(audio) else 0.0
    db = 20 * np.log10(rms) if rms > 0 else -120.0
    print(f"  captured {len(audio) / SR:.2f}s · peak {peak:.3f} · rms {rms:.4f} ({db:.1f} dBFS)")
    if peak < 0.005:
        print("  WARNING: almost no sound reached the mic.")
        print("  Check Microphone permission in System Settings -> Privacy & Security -> Microphone,")
        print("  the microphone named above, and that you spoke during the recording.")
        return False
    return True


def cosine(a, b) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


def capture(phrases: list[str]):
    """Record each phrase (retrying near-silent clips). Returns (clips, embeddings)."""
    clips, embeddings = [], []
    for i, phrase in enumerate(phrases, 1):
        while True:
            input(f"\n{i}/{len(phrases)} Press Enter, then say: \"{phrase}\"")
            print("  recording...")
            try:
                audio = record()
            except Exception as e:
                sys.exit(f"  Recording failed: {e}")
            if report(audio) or input("  Keep this clip anyway? [y/N] ").strip().lower() == "y":
                break
        try:
            vec = np.asarray(voiceprint.embed(audio), dtype="float32")
        except Exception:
            traceback.print_exc()
            sys.exit("  Could not embed that clip; see the traceback above.")
        clips.append(audio)
        embeddings.append(vec)
    return clips, embeddings


def show_scores(clips) -> int:
    """Score clips like Monday does at runtime and print pass / FAIL for each."""
    mean = voiceprint.load()[0]
    ok = 0
    for i, c in enumerate(clips, 1):
        wav = voiceprint._speech(c)
        sec = len(wav) / voiceprint.SR
        s, need = voiceprint.score(wav, mean), voiceprint.needed(sec)
        ok += s >= need
        print(f"  clip {i}: {s:.2f} (need {need:.2f}, {sec:.1f}s of speech)  {'pass' if s >= need else 'FAIL'}")
    return ok


def main(mode: str = "") -> int:
    check_setup()
    if mode in ("check", "add") and voiceprint.load() is None:
        sys.exit("No voice enrolled yet. Run: monday enroll")
    if mode == "check":
        print("\nRoom check: say three short commands here, the way you talk to Monday.")
        clips, _ = capture(CHECK_PHRASES)
        ok = show_scores(clips)
        print(f"\n{ok}/{len(clips)} recognised as you.")
        if ok < len(clips):
            print("  Teach Monday your voice in this room:  monday enroll --add")
        return 0
    if mode == "add":
        print("\nAdding clips recorded in this room to your voiceprint (keeps your earlier clips).")
        clips, new = capture(PHRASES[:3])
        old = voiceprint.load()
        need = voiceprint.save(list(old[2]) + new, clips, max_need=old[1])
        print(f"\nUpdated. Needed score is now {need:.2f}. These clips against it:")
        show_scores(clips)
        return 0
    print("Voice enrollment: press Enter, then say the phrase while it records for 4 seconds.")
    clips, embeddings = capture(PHRASES)
    sims = [cosine(embeddings[i], embeddings[j]) for i in range(len(embeddings)) for j in range(i + 1, len(embeddings))]
    print(f"\nConsistency of your five clips: min {min(sims):.2f} · mean {sum(sims) / len(sims):.2f}")
    if min(sims) < 0.70:
        print("  WARNING: the clips differ a lot. Re-run somewhere quieter, same distance and tone.")
    need = voiceprint.save(embeddings, clips)
    print(f"\nVoice enrolled. Needed score {need:.2f}. Saved to {voiceprint.PATH}")
    print("Self-check (each clip scored the way Monday scores you):")
    show_scores(clips)
    print("\nNow set FORGE_VOICE_LOCK=1 in .env and restart Monday. In a noisy room run:  monday enroll --check")
    return 0
