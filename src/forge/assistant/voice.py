"""Voice, fully on device with open models: a faithful port of Monday's listening engine.

Ears      push-to-talk recording -> Whisper on the Apple GPU (mlx-whisper; faster-whisper on the CPU elsewhere)
Listener  always-on: "Hey Monday" or a double clap wakes it; while it works or talks you can just speak over it
Mouth     Kokoro-82M, sentence by sentence (it starts talking while the model is still writing)

Listening rules (do not drift, see Monday's AGENTS.md section 5):
- a 30 ms frame is speech only if webrtcvad says so AND it stands out from the room's noise (the quietest 20% of
  the last 3 s); a phrase starts after 4 speech frames in 6 and ends after END_MS without speech
- under 240 ms of speech is dropped; only the spoken part goes to Whisper; unsure Whisper pieces are dropped
- a command needs the wake word, a double clap, or the conversation window (busy, talking, CONVERSE_S after a reply)
- while busy or talking, a phrase without the wake word needs two real words or a stop word
- the same command again within 20 s without the wake word is ignored (feedback loops)
- unfinished sentences wait FINISH_S for the rest; Forge's own voice is filtered out (number words normalised)
Every part reports a 0..1 level so the orb and the screen edges breathe with the voices."""
import difflib
import os
import queue
import random
import re
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

import numpy as np

from ..config import PROFILE

RATE = SR = 16000
FRAME = 480  # 30 ms
HOME = Path.home() / ".forge"
MODELS = HOME / "models"
KOKORO_FILES = {
    "kokoro-v1.0.onnx": "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx",
    "voices-v1.0.bin": "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin",
}


def _env(name: str, default: str) -> str:
    return os.getenv(f"FORGE_{name}", default)


# Whisper large-v3-turbo (the hardware profile's choice from 16 GB up), like Monday: smaller models mishear commands
# and invent words from room noise. "fast" (small.en, ~0.2 s instead of ~0.8 s) is opt-in.
STT_ALIASES = {"fast": "mlx-community/whisper-small.en-mlx", "accurate": "mlx-community/whisper-large-v3-turbo"}
STT_MODEL = STT_ALIASES.get(_env("STT_MODEL", ""), _env("STT_MODEL", "")) or PROFILE.stt_model
WHISPER_LANG = _env("WHISPER_LANG", "en") or None
TTS = _env("TTS", "kokoro")  # kokoro (natural, on device) or say
TTS_VOICE = _env("TTS_VOICE", "af_bella*0.6+af_sky*0.4")  # one voice, or a weighted blend
TTS_SPEED = float(_env("TTS_SPEED", "1.12"))
CLAP = _env("CLAP", "1") == "1"  # double-clap wake
NOISE_REDUCE = _env("NOISE_REDUCE", "1") == "1"  # clean each phrase before Whisper
VOICE_LOCK = _env("VOICE_LOCK", "0") == "1"  # only the enrolled voice can command (forge enroll)
VAD_LEVEL = int(_env("VAD_LEVEL", "3"))  # speech detector strictness 0-3: 3 ignores the most room noise
END_FRAMES = int(_env("END_MS", "550")) // 30  # time without speech that ends a phrase
END_S = END_FRAMES * FRAME / RATE
FINISH_S = float(_env("FINISH_S", "1.8"))  # extra wait when a sentence sounds unfinished ("I want you to...")
CONVERSE_S = float(_env("CONVERSE_S", "8"))  # after a reply, talk again without the wake word
ACK = _env("ACK", "chime")  # what Forge does on wake: chime (instant) or speak
DEBUG = _env("DEBUG", "0") == "1"  # one line per phrase explaining what was heard and why (~/.forge/debug.log)
VOCAB = _env("STT_VOCAB", "Monday, YouTube, Excalidraw, Safari, Chrome, Spotify, WhatsApp, VS Code, Finder.")

SPEAKING = threading.Event()  # a Mouth is playing audio right now (the chime waits for it to release the speaker)
_mouths: list = []  # every Mouth, so preload() can warm their voice
SHORT_OK = re.compile(r"^\W*(stop|cancel|wait|pause|enough|quiet)\W*$", re.I)  # may interrupt alone
ANSWER_OK = re.compile(r"^\W*(yes|yeah|yep|yup|sure|ok|okay|no|nope|do it|go ahead|don't)\W*$", re.I)
FILLER = set("hmm hm mm mhm uh um ah oh okay ok yeah yes no nope so like right well huh hey".split())
DADDY = re.compile(r"\bwake up\W+daddy'?s home\b", re.I)
JUNK = {"bye bye", "music", "applause", "laughter", "", "you", "thank you", "thanks for watching",
        "thank you for watching", "bye", "so", "uh", "um", "hmm", "oh"}
ACKS = ["Yes?", "Hi! I'm listening.", "Mm-hmm?", "I'm here!", "Yes yes?", "Tell me!"]
DANGLING = re.compile(r"(?:\.\.\.|…|-)\s*$|\b(?:to|and|the|a|an|like|so|but|or|of|with|for|my|your|um|uh|that|this|is|"
                      r"can you|could you|please|want|i|i'm|then|also|in|on|about)[,\s]*$", re.I)
# push-to-talk keys on macOS: name -> (key code, is a modifier key)
PTT_KEYS = {"alt_r": (61, True), "alt_l": (58, True), "cmd_r": (54, True), "cmd_l": (55, True),
            "ctrl_r": (62, True), "ctrl_l": (59, True), "shift_r": (60, True), "shift_l": (56, True),
            "fn": (63, True), "caps_lock": (57, True), "f5": (96, False), "f6": (97, False), "f7": (98, False),
            "f8": (100, False), "f9": (101, False), "f10": (109, False), "f11": (103, False), "f12": (111, False),
            "f13": (105, False), "f14": (107, False), "f15": (113, False), "f16": (106, False), "f17": (64, False),
            "f18": (79, False), "f19": (80, False)}
KEY_NAMES = {"alt_r": "right Option", "alt_l": "left Option", "cmd_r": "right Command", "cmd_l": "left Command",
             "ctrl_r": "right Control", "ctrl_l": "left Control", "shift_r": "right Shift", "shift_l": "left Shift",
             "fn": "Fn", "caps_lock": "Caps Lock"}


VARIANTS = {"monday": "monday|munday|mundy|mon day|mondays",  # Monday's own list (monday/voice.py WAKE)
            "forge": "forge|forj|forged|forges|fords?|fourge|gorge"}


def wake_pattern(name: str = "Monday") -> re.Pattern:
    """ "Hey Monday, ..." and the spellings Whisper writes for it. group(1) is what follows the wake word."""
    n = re.escape(name.lower())
    alts = VARIANTS.get(name.lower(), n)
    return re.compile(r"^\W*(?:(?:hey|hi|hello|ok|okay|yo|oi|wake up|good morning)\W+)*"
                      rf"(?:{alts})\b\W*(.*)$", re.I)


def dbg(msg: str):
    """Debug line (FORGE_DEBUG=1): printed and kept in ~/.forge/debug.log."""
    if not DEBUG:
        return
    line = f"[mic {time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        HOME.mkdir(parents=True, exist_ok=True)
        with open(HOME / "debug.log", "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def _db(x: float) -> str:
    return f"{20 * np.log10(max(x, 1e-6)):.0f}"


# ---------- speech to text: Whisper on the Apple GPU, faster-whisper elsewhere ----------

_whisper_lock = threading.Lock()
_cpu_model = None
_denoise_warned = False
_last: dict = {}  # what happened to the last phrase, for debug lines


def denoise(audio: np.ndarray) -> np.ndarray:
    """Optional noise reduction. Returns the audio unchanged if disabled or unavailable."""
    global _denoise_warned
    if not NOISE_REDUCE or len(audio) == 0:
        return audio
    try:
        import noisereduce
    except ImportError:
        return audio  # the optional voicelock extra is not installed: nothing to warn about
    try:
        out = noisereduce.reduce_noise(y=audio, sr=SR, stationary=False, prop_decrease=0.85)
        out = np.asarray(out, dtype=audio.dtype)
        return out if out.shape == audio.shape else audio
    except Exception as e:
        if not _denoise_warned:
            _denoise_warned = True
            print("noise reduction skipped:", str(e)[:80])
        return audio


def _cuda() -> bool:
    """An NVIDIA GPU that CTranslate2 (faster-whisper) can use. FORGE_STT_DEVICE=cpu turns it off."""
    if _env("STT_DEVICE", "auto") == "cpu":
        return False
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def _segments(audio: np.ndarray, prompt: str | None) -> list[tuple[str, float, float]]:
    """(text, no_speech_prob, avg_logprob) for each piece Whisper heard."""
    global _cpu_model
    try:
        import mlx_whisper
    except ImportError:
        from faster_whisper import WhisperModel
        if _cpu_model is None:
            name = STT_MODEL.rsplit("/", 1)[-1].removeprefix("whisper-").removesuffix("-mlx")
            if _cuda():
                _cpu_model = WhisperModel(name, device="cuda", compute_type="float16")
            else:  # int8 on the CPU; more than ~8 threads stops helping for a few seconds of audio
                _cpu_model = WhisperModel(name, device="cpu", compute_type="int8",
                                          cpu_threads=min(8, os.cpu_count() or 4))
        out, _ = _cpu_model.transcribe(audio, language=WHISPER_LANG, beam_size=1, vad_filter=False,
                                       condition_on_previous_text=False, initial_prompt=prompt,
                                       without_timestamps=True)
        return [(s.text, s.no_speech_prob, s.avg_logprob) for s in out]
    # greedy (temperature 0, no fallback re-decoding) and no timestamps: the fastest decode
    r = mlx_whisper.transcribe(audio, path_or_hf_repo=STT_MODEL, language=WHISPER_LANG, verbose=None,
                               temperature=0.0, condition_on_previous_text=False, initial_prompt=prompt,
                               without_timestamps=True)
    return [(s["text"], s.get("no_speech_prob", 0), s.get("avg_logprob", 0)) for s in r["segments"]]


def _transcribe(audio: np.ndarray, prompt: str | None = None, warmup: bool = False, trusted: bool = False,
                lock: bool = True) -> str:
    _last.clear()
    audio = np.asarray(audio, dtype=np.float32)
    if not warmup:
        audio = denoise(audio)  # clean once; the voice check and Whisper both use the cleaned audio
        if VOICE_LOCK and lock:
            from . import voiceprint
            t = time.time()
            ok = voiceprint.is_owner(audio, trusted)
            _last["lock"] = f"voice {voiceprint.last} {'you' if ok else 'NOT you'} {int((time.time() - t) * 1000)} ms"
            if not ok:
                _last["why"] = "not your voice"
                return ""
    t = time.time()
    with _whisper_lock:
        segs = _segments(audio, prompt)
    _last["stt_ms"] = int((time.time() - t) * 1000)
    # Whisper invents words from noise; drop pieces it is unsure about
    kept = [s.strip() for s, p, lp in segs if p < 0.6 and lp > -1.0]
    text = " ".join(" ".join(kept).split())
    if segs and not kept:
        _last["why"] = f"Whisper unsure, probably noise (\"{' '.join(s.strip() for s, *_ in segs)[:40]}\")"
    words = re.findall(r"[\w']+", text.lower())
    if len(words) >= 6 and len(set(words)) / len(words) < 0.5:  # "I'm sorry, I'm sorry, ...": Whisper looping on noise
        _last["why"] = f"repeated words, Whisper looping on noise (\"{text[:40]}\")"
        return ""
    if text.lower().strip(" .!?,") in JUNK:
        _last["why"] = f"noise word \"{text}\""
        return ""
    return text


def transcribe(audio: np.ndarray) -> str:
    """Push-to-talk transcription: holding the key already proves it is you, so no voice lock."""
    if len(audio) < SR * 0.4:
        dbg("talk key tap too short, ignored")
        return ""
    text = _transcribe(audio, prompt=VOCAB, lock=False)
    dbg(f"whisper {_last.get('stt_ms', 0)} ms \"{text}\"" if text else f"-> dropped: {_last.get('why', 'no words')}")
    return text


def preload():
    """Load Whisper (and the voiceprint) once so the first command is not slow (downloads it the first time)."""
    _transcribe(np.zeros(SR // 2, "float32"), warmup=True)
    for m in _mouths:
        m.warm()
    if VOICE_LOCK:
        from . import voiceprint
        voiceprint.preload()


# ---------- the wake chime ----------

_pop = None


def chime():
    """A soft bubble pop, generated once. It waits for cut-off speech to release the speaker first, or the
    speech stop would cut the chime too. With FORGE_ACK=speak the Listener speaks an acknowledgement instead."""
    global _pop
    if ACK == "speak":
        return
    import sounddevice as sd
    for _ in range(25):
        if not SPEAKING.is_set():
            break
        time.sleep(0.02)
    if _pop is None:
        sr = 24000
        t = np.arange(int(sr * 0.16)) / sr
        f = 520 + 900 * (1 - np.exp(-t * 40))
        _pop = (0.22 * np.sin(2 * np.pi * np.cumsum(f) / sr) * np.exp(-t * 26)
                + 0.08 * np.sin(2 * np.pi * 1760 * t) * np.exp(-t * 45)).astype("float32"), sr
    try:
        sd.play(*_pop)
    except Exception as e:
        print("chime failed:", e)


# ---------- echo: Forge hearing its own voice ----------

_NUMS = {w: str(i) for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                         "fourteen fifteen sixteen seventeen eighteen nineteen twenty".split())}
_NUMS.update({"thirty": "30", "forty": "40", "fifty": "50", "sixty": "60", "ninety": "90", "hundred": "100"})


def _norm(w: str) -> str:
    """Comparable form of a word: lower case, digits for number words ("five" = "5"), no plural s."""
    w = re.sub(r"[^a-z0-9']", "", w.lower())
    w = _NUMS.get(w, w)
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w


def _is_echo(text: str, recent: str) -> bool:
    """A short transcript made almost only of words Forge just said, like "five minutes" after
    "Timer set for 5 minutes"."""
    ref = {_norm(w) for w in recent.split()}
    words = [_norm(w) for w in text.split() if _norm(w)]
    if not ref or not words or len(words) > 8:
        return False
    if any(w.isdigit() and w not in ref for w in words):
        return False  # a different number ("10 minutes" after "5 minutes") means it is really you
    return sum(w in ref for w in words) / len(words) >= 0.75


def _strip_echo(text: str, recent: str) -> str:
    """Remove the parts of a transcript that are Forge's own voice coming back through the mic."""
    if not recent.strip():
        return text
    words = text.split()
    tw, rw = [_norm(w) for w in words], [_norm(w) for w in recent.split()]
    keep = [True] * len(words)
    for a, _, n in difflib.SequenceMatcher(None, tw, rw, autojunk=False).get_matching_blocks():
        if n >= 2 or (n == 1 and a == 0 and len(rw) <= 4):
            keep[a:a + n] = [False] * n
    return " ".join(w for w, k in zip(words, keep, strict=True) if k).strip(" ,.!?")


def _double_clap(lv: np.ndarray, noise: float) -> bool:
    """Two sharp hits 0.1 to 0.8 s apart, each at most 90 ms."""
    hi = lv.max()
    if hi < 0.06 or (lv > max(noise * 4, 0.012)).sum() > 16:  # quiet, or too long to be claps
        return False
    loud = np.flatnonzero(lv > hi * 0.35)
    runs, start = [], loud[0]
    for a, b in zip(loud, loud[1:], strict=False):
        if b - a > 1:
            runs.append((start, a))
            start = b
    runs.append((start, loud[-1]))
    if len(runs) != 2:
        return False
    (s1, e1), (s2, e2) = runs
    return e1 - s1 <= 3 and e2 - s2 <= 3 and 3 <= s2 - e1 <= 27


# ---------- push-to-talk ----------

class Ears:
    """Push-to-talk: start() opens the mic, stop() returns the transcript (taps under 0.4 s are ignored)."""

    def __init__(self, on_level: Callable[[float], None] = lambda v: None):
        self.on_level, self.frames, self.stream = on_level, [], None
        self.lock = threading.Lock()

    def start(self):
        import sounddevice as sd
        with self.lock:
            if self.stream:
                return
            self.frames = []

            def cb(indata, n, t, status):
                self.frames.append(indata[:, 0].copy())
                self.on_level(min(1.0, float(np.sqrt(np.mean(indata ** 2))) * 12))
            self.stream = sd.InputStream(samplerate=RATE, channels=1, dtype="float32", callback=cb, blocksize=800)
            self.stream.start()
            dbg("talk key down: recording")

    def stop(self) -> str:
        with self.lock:
            if not self.stream:
                return ""
            self.stream.stop()
            self.stream.close()
            self.stream = None
            audio = np.concatenate(self.frames) if self.frames else np.zeros(0, np.float32)
        self.on_level(0)
        dbg(f"talk key up: {len(audio) / SR:.1f}s recorded")
        return transcribe(audio)


# ---------- text to speech: Kokoro on device, macOS say as fallback ----------

_NOT_SPOKEN = re.compile(r"[^\w\s.,!?;:'\"()%$&@#+/=-]")  # emojis and symbols the voice would stumble over


class Mouth:
    """Speaks queued sentences. Synthesis of the next sentence overlaps playback of the current one.
    on_say gets each sentence as it starts playing (the caption)."""

    def __init__(self, on_level: Callable[[float], None] = lambda v: None,
                 on_speaking: Callable[[bool], None] = lambda on: None, on_say: Callable[[str], None] = lambda t: None):
        self.on_level, self.on_speaking, self.on_say = on_level, on_speaking, on_say
        self.kokoro, self.style = None, None
        self.cache: dict[str, tuple[np.ndarray, int]] = {}
        self.text_q: queue.Queue = queue.Queue()
        self.audio_q: queue.Queue = queue.Queue(maxsize=3)
        self.gen = 0  # bumped by interrupt(): anything queued under an older generation is dropped
        self.busy = 0
        self.saying = ""  # what is playing now, for the echo filter
        self.said: deque[tuple[str, float]] = deque(maxlen=4)  # what was said lately, and when
        self.recent: deque[str] = deque(maxlen=6)
        self.mute_until = 0.0  # the mic ignores the moment right after speech
        self.idle = threading.Event()
        self.idle.set()
        self.lock = threading.Lock()
        threading.Thread(target=self._synth_loop, daemon=True).start()
        threading.Thread(target=self._play_loop, daemon=True).start()
        _mouths.append(self)

    @property
    def speaking(self) -> bool:
        return not self.idle.is_set()

    def _engine(self):
        if TTS != "kokoro":
            return None
        if self.kokoro is None and all((MODELS / f).exists() for f in KOKORO_FILES):
            try:
                import onnxruntime
                onnxruntime.set_default_logger_severity(3)  # errors only
            except Exception:
                pass
            from kokoro_onnx import Kokoro
            self.kokoro = Kokoro(str(MODELS / "kokoro-v1.0.onnx"), str(MODELS / "voices-v1.0.bin"))
            self.style = self._blend(TTS_VOICE)
        return self.kokoro

    def _blend(self, spec: str):
        """ "af_bella" or a blend like "af_bella*0.6+af_sky*0.4" (weights are normalised)."""
        parts = [p.split("*") for p in spec.replace(" ", "").split("+") if p]
        if len(parts) == 1:
            return parts[0][0]
        total = sum(float(p[1]) if len(p) > 1 else 1.0 for p in parts)
        return sum(self.kokoro.get_voice_style(p[0]) * (float(p[1]) if len(p) > 1 else 1.0) / total for p in parts)

    def _synth(self, text: str):
        if text in self.cache:
            return self.cache[text]
        k = self._engine()
        if k is None:
            return None, 0
        lang = "en-gb" if TTS_VOICE[:1] == "b" else "en-us"
        a, sr = k.create(text, voice=self.style, speed=TTS_SPEED, lang=lang)
        a = np.asarray(a, dtype=np.float32)
        if len(text) < 60:  # common short phrases start instantly the next time
            self.cache[text] = (a, sr)
        return a, sr

    def warm(self):
        """Synthesise the acknowledgements once, so they play with no delay."""
        try:
            for s in [*ACKS, "Okay!", "Okay.", "Okay, cancelled.", "Done!"]:
                self._synth(s)
        except Exception as e:
            print("kokoro warmup failed:", e)

    def say(self, text: str):
        text = clean_for_speech(text)
        text = re.sub(r"\s+", " ", _NOT_SPOKEN.sub("", text)).strip()
        if text:
            with self.lock:
                self.busy += 1
                self.idle.clear()
            self.text_q.put((self.gen, text))

    def _done_one(self):
        with self.lock:
            self.busy = max(0, self.busy - 1)
            if self.busy == 0:
                self.idle.set()
                self.on_speaking(False)
                self.on_level(0)

    def _synth_loop(self):
        while True:
            gen, text = self.text_q.get()
            if gen != self.gen:
                self._done_one()
                continue
            try:
                samples, sr = self._synth(text)
                self.audio_q.put((gen, text, samples, sr))
            except Exception as e:
                print("kokoro error, using say:", e)
                self.audio_q.put((gen, text, None, 0))

    def _play_loop(self):
        while True:
            gen, text, samples, sr = self.audio_q.get()
            try:
                if gen == self.gen:
                    self.saying = text
                    self.recent.append(text)
                    SPEAKING.set()
                    self.on_speaking(True)
                    self.on_say(text)
                    if samples is None:
                        self._say_fallback(text, gen)
                    else:
                        self._play(samples, sr, gen)
            except Exception as e:
                print("tts error:", e)
            finally:
                if self.saying:
                    self.said.append((self.saying, time.time()))
                self.saying = ""
                self.mute_until = time.time() + 0.3
                SPEAKING.clear()
                self._done_one()

    def _say_fallback(self, text: str, gen: int):
        """The system voice, when Kokoro is not installed: macOS `say`, or Windows SAPI through PowerShell."""
        if sys.platform == "win32":
            script = ("Add-Type -AssemblyName System.Speech; "
                      "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                      "$s.Rate = 2; $s.Speak($env:MONDAY_SAY)")
            p = subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                                 env={**os.environ, "MONDAY_SAY": text}, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            p = subprocess.Popen(["say", "-r", "205", text])
        while p.poll() is None:
            if gen != self.gen:
                p.terminate()
                break
            self.on_level(0.35 + 0.25 * np.sin(time.time() * 18))
            time.sleep(0.05)

    def _play(self, samples: np.ndarray, sr: int, gen: int):
        import sounddevice as sd
        pos, done = [0], threading.Event()

        def cb(out, n, t, status):
            if gen != self.gen:
                out[:] = 0
                raise sd.CallbackStop
            chunk = samples[pos[0]:pos[0] + n]
            out[:len(chunk), 0] = chunk
            out[len(chunk):, 0] = 0
            pos[0] += n
            self.on_level(min(1.0, float(np.sqrt(np.mean(chunk ** 2))) * 6) if len(chunk) else 0)
            if pos[0] >= len(samples):
                raise sd.CallbackStop
        with sd.OutputStream(samplerate=sr, channels=1, dtype="float32", callback=cb, blocksize=sr // 30,
                             finished_callback=done.set):
            done.wait(len(samples) / sr + 2)

    def interrupt(self):
        """Stop talking now and drop everything queued."""
        self.gen += 1

    hush = interrupt

    def wait(self, timeout: float = 60):
        self.idle.wait(timeout)

    def recent_speech(self, window: float = 6.0) -> str:
        now = time.time()
        return " ".join([self.saying] + [t for t, at in self.said if now - at < window])

    def asked(self, window: float = 30.0) -> bool:
        """Forge just asked something ("Should I go ahead?"), so a bare yes or no is an answer."""
        now = time.time()
        return any("?" in t for t, at in self.said if now - at < window) or "?" in self.saying

    def is_echo(self, heard: str) -> bool:
        """True when the mic just heard Forge's own voice coming out of the speakers."""
        return _is_echo(heard, self.recent_speech())

    def strip_echo(self, heard: str) -> str:
        return _strip_echo(heard, self.recent_speech())


class SentenceSplitter:
    """Feed streamed text; get whole sentences back as soon as they are complete."""

    def __init__(self):
        self.buf = ""

    def feed(self, text: str) -> list[str]:
        self.buf += text
        out = []
        while True:
            m = re.search(r"(.{12,}?[.!?])(\s+|$)(?=\S|$)", self.buf, re.S)
            if not m or m.end(1) == len(self.buf):
                break
            out.append(self.buf[:m.end(1)].strip())
            self.buf = self.buf[m.end():]
        return out

    def flush(self) -> list[str]:
        rest, self.buf = self.buf.strip(), ""
        return [rest] if rest else []


def clean_for_speech(text: str) -> str:
    """Markdown and links read terribly out loud."""
    t = re.sub(r"```.*?```", " (see the code on screen) ", text, flags=re.S)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"[*_`#>|]+", "", t)
    t = re.sub(r"^\s*[-•]\s+", "", t, flags=re.M)
    return " ".join(t.split())


# ---------- the wake engine: "Hey Monday" or a double clap, always listening ----------

def _vad():
    """webrtcvad speech detector, or None (then loudness alone decides)."""
    try:
        import webrtcvad
        return webrtcvad.Vad(VAD_LEVEL)
    except Exception as e:
        print("speech detector off, using loudness:", str(e)[:80])
        return None


class Listener:
    """Always-on listening, Monday's engine: speech detection, wake word or double clap, conversation window,
    echo filter, unfinished-sentence wait, loop guard and barge-in.

    on_command(text) gets each command; on_wake() is called when Forge should listen (it plays the chime);
    busy() says whether a task is running; on_debug(line) gets the explanation of every phrase."""

    def __init__(self, name: str, on_command: Callable[[str], None], on_wake: Callable[[], None],
                 on_level: Callable[[float], None], busy: Callable[[], bool], mouth: Mouth,
                 on_debug: Callable[[str], None] | None = None):
        self.name = name
        self.on_command, self.on_wake, self.on_level, self.busy, self.mouth = on_command, on_wake, on_level, busy, mouth
        self.on_debug = on_debug or (lambda line: None)
        self.wake = wake_pattern(name)
        self.follow_until = self.converse_until = self.expect_until = self.mute_until = 0.0
        self.pending: tuple[str, float] | None = None  # a sentence that sounded unfinished, waiting for the rest
        self.last_cmd = ["", 0.0]  # last command heard, to ignore an instant repeat that came without the wake word
        self.stop_ev = threading.Event()
        self.thread: threading.Thread | None = None
        self.stream = None

    # ---- control ----
    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_ev.clear()
        self.thread = threading.Thread(target=self._engine, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_ev.set()

    def converse(self, seconds: float = CONVERSE_S):
        """Listen without the wake word for this long (after a reply)."""
        self.converse_until = time.time() + seconds

    def expect(self, seconds: float = 8):
        """Forge asked a question: the next phrase is the answer, whatever it is ("yes" alone counts)."""
        self.expect_until = time.time() + seconds

    def end_conversation(self):
        """Esc: stop listening without the wake word until Forge is woken again."""
        self.converse_until = self.follow_until = self.expect_until = 0.0
        self.pending = None

    def interrupt_listen(self) -> bool:
        """A single tap of the interrupt key while Forge is busy or speaking: stop and listen."""
        if not (self.busy() or self.mouth.speaking):
            return False
        self._dbg("space: interrupted, listening")
        self._woke()
        return True

    # ---- state ----
    def _dbg(self, msg: str):
        dbg(msg)
        self.on_debug(msg)

    def _talking(self) -> bool:
        return self.mouth.speaking or time.time() < max(self.mute_until, self.mouth.mute_until)

    def _conversing(self) -> bool:
        """While Forge is busy, talking, or just answered, no wake word is needed."""
        return self.busy() or self.mouth.speaking or time.time() < self.converse_until

    def _expecting_answer(self) -> bool:
        return time.time() < self.expect_until

    def _woke(self):
        self.follow_until = time.time() + 10
        self.on_wake()  # interrupts speech and plays the chime once the speaker is free
        if ACK == "speak":
            self.mouth.say(random.choice(ACKS))
        self.mute_until = time.time() + 0.25

    # ---- the engine ----
    def _engine(self):
        import sounddevice as sd
        frames: queue.Queue = queue.Queue()
        self.stream = sd.InputStream(samplerate=SR, channels=1, dtype="float32", blocksize=FRAME,
                                     callback=lambda d, n, t, s: frames.put(d[:, 0].copy()))
        self.stream.start()
        vad = _vad()
        self._dbg(f"listening: speech detector {'level ' + str(VAD_LEVEL) if vad else 'off'}, end after "
                  f"{END_FRAMES * 30} ms of no speech, voice lock {'on' if VOICE_LOCK else 'off'}")
        noise, pre, seg, lv, vf, quiet = 0.004, deque(maxlen=10), None, [], [], 0
        ring, last_report, room = deque(maxlen=6), time.time(), deque(maxlen=100)
        try:
            while True:
                try:
                    f = frames.get(timeout=0.5) if not self.stop_ev.is_set() else frames.get_nowait()
                except queue.Empty:
                    if self.stop_ev.is_set():
                        break  # stopped, and every buffered frame has been heard
                    if seg is None and self.pending:
                        self._flush_pending()
                    continue
                level = float(np.sqrt(np.mean(f * f)))
                talking = self._talking()
                if not talking:
                    self.on_level(min(1.0, level * 12))
                    room.append(level)
                    if len(room) >= 20 and len(room) % 5 == 0:
                        # the room's noise = the quietest 20% of the last 3 s (the gaps between words reveal it)
                        noise = max(0.002, float(np.percentile(room, 20)))
                # a frame counts as speech when the detector says so AND it stands out from the room's noise
                voiced = level > max(noise * (2.5 if talking else 1.6), 0.006) and (
                    vad.is_speech((np.clip(f, -1, 1) * 32767).astype("<i2").tobytes(), SR) if vad
                    else level > noise * 4)
                if seg is None:
                    if self.pending:
                        self._flush_pending()
                    if DEBUG and time.time() - last_report > 15:
                        last_report = time.time()
                        self._dbg(f"room noise {_db(noise)} dB")
                    pre.append(f)
                    ring.append(voiced)
                    clap_like = level > max(noise * 8, 0.05)  # claps are not speech, so loudness can start them
                    if sum(ring) >= 4 or clap_like:  # 4 of the last 6 frames are speech: someone is talking
                        seg, lv, vf, quiet = list(pre), [level], list(ring)[-len(pre):], 0
                        vf = [False] * (len(pre) - len(vf)) + vf
                    continue
                seg.append(f)
                lv.append(level)
                vf.append(voiced)
                quiet = 0 if voiced else quiet + 1
                if quiet >= END_FRAMES or len(seg) > 330:  # no speech for END_MS ends the phrase, 10 s max
                    try:
                        self._segment(np.concatenate(seg), np.array(lv), noise, np.array(vf))
                    except Exception as e:
                        print("wake engine error:", e)
                    seg = None
                    pre.clear()
                    ring.clear()
        finally:
            self.stream.stop()
            self.stream.close()
            self.stream = None

    def _segment(self, audio: np.ndarray, lv: np.ndarray, noise: float, vf: np.ndarray | None = None):
        t0 = time.time()
        expecting = (self._expecting_answer() or time.time() < self.follow_until or self._conversing()
                     or self.pending is not None)
        if CLAP and not expecting and _double_clap(lv, noise):
            self._dbg("double clap -> wake")
            return self._woke()
        vf = np.ones(len(lv), bool) if vf is None else vf
        speech = int(vf.sum())
        info = (f"{len(lv) * 0.03:.1f}s, speech {speech * 0.03:.1f}s ({100 * speech // max(1, len(lv))}%), "
                f"noise {_db(noise)} dB, peak {_db(float(lv.max()))} dB")
        if speech < 8:  # under 240 ms of real speech: a bump, a cough, the TV
            return self._dbg(f"{info} -> dropped: not enough speech")
        # keep only the spoken part (plus a little padding), so room noise around it does not count
        idx = np.flatnonzero(vf)
        a0, a1 = max(0, idx[0] - 4) * FRAME, min(len(lv), idx[-1] + 5) * FRAME
        heard = _transcribe(audio[a0:a1], prompt=VOCAB if expecting else None, trusted=expecting)
        info += f" | {_last.get('lock', 'no lock')} | whisper {_last.get('stt_ms', 0)} ms"
        if not heard:
            return self._dbg(f"{info} -> dropped: {_last.get('why', 'no words')}")
        self._text(heard, info, t0)

    def _text(self, heard: str, info: str = "", t0: float | None = None):
        """A transcript from the engine: echo filter, unfinished-sentence wait, then routing."""
        if self.mouth.is_echo(heard):
            return self._dbg(f"{info} \"{heard}\" -> dropped: my own voice")
        text = self.mouth.strip_echo(heard)
        if len(re.findall(r"\w+", text)) == 0:
            return self._dbg(f"{info} \"{heard}\" -> dropped: only my own words")
        if self.pending:  # the rest of an unfinished sentence
            text, self.pending = self.pending[0] + " " + text, None
        if DANGLING.search(text) and not self._expecting_answer():
            self.pending = (text, time.time())  # wait a moment for them to finish the thought
            return self._dbg(f"{info} \"{text}\" -> waiting: sounds unfinished")
        self._dbg(f"{info} \"{text}\"" + (f" ({int((time.time() - t0) * 1000)} ms total)" if t0 else ""))
        self._route(text)

    def _flush_pending(self):
        if self.pending and time.time() - self.pending[1] > FINISH_S:
            text, self.pending = self.pending[0], None
            self._route(text.rstrip(". …"))

    def _route(self, text: str):
        if self._expecting_answer():
            self._dbg("-> answer to my question")
            self.expect_until = 0.0
            return self.on_command(text)
        m = self.wake.match(text)
        if time.time() < self.follow_until or self._conversing():
            followed = time.time() < self.follow_until
            self.follow_until = 0.0
            if m and not m.group(1).strip():  # just "Forge?" while busy: stop and listen
                return self._woke()
            cmd = m.group(1).strip() if m else text
            real = [w for w in re.findall(r"[\w']+", cmd.lower()) if w not in FILLER]
            answer = ANSWER_OK.match(cmd) and self.mouth.asked()
            if (self.busy() or self.mouth.speaking) and not m and len(real) < 2 and not SHORT_OK.match(cmd) \
                    and not answer:
                return self._dbg(f"\"{cmd}\" -> ignored: too little to interrupt (say \"{self.name}, ...\" "
                                 "or tap Space)")
            key = " ".join(_norm(w) for w in cmd.split())
            if not m and not followed and key == self.last_cmd[0] and time.time() - self.last_cmd[1] < 20:
                return self._dbg(f"\"{cmd}\" -> ignored: same command again without the wake word")
            self.last_cmd[:] = [key, time.time()]
            return self.on_command(cmd)
        if DADDY.search(text):
            return self._woke()
        if not m:
            return self._dbg(f"\"{text}\" -> ignored: no wake word (say \"Hey {self.name}\")")
        rest = m.group(1).strip()
        if len(rest.split()) >= 2:  # "Hey Monday, open YouTube" in one breath
            self.last_cmd[:] = [" ".join(_norm(w) for w in rest.split()), time.time()]
            return self.on_command(rest)
        self._woke()


def download_voice(progress: Callable[[str], None] = print):
    """Fetch the Kokoro model files (about 340 MB) into ~/.forge/models."""
    import httpx
    MODELS.mkdir(parents=True, exist_ok=True)
    for name, url in KOKORO_FILES.items():
        dest = MODELS / name
        if dest.exists():
            continue
        progress(f"downloading {name}")
        tmp = dest.with_suffix(".part")
        with httpx.stream("GET", url, follow_redirects=True, timeout=60) as r, tmp.open("wb") as f:
            r.raise_for_status()
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
        tmp.rename(dest)
