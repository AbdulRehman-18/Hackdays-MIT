"""Speaker lock: enroll the owner's voice once, then only that voice can command Forge.

A phrase is cut into overlapping 1.6 s pieces and scored by its best piece, so a few clear words count even when the
rest is room noise, another person or Forge's own voice. One threshold, calibrated at enrollment from pieces of
your own clips; short phrases and phrases inside a conversation you started get a little slack.
`forge enroll --check` tests the current room, `--add` teaches it your voice in that room. Every failure fails
open: a broken check must never lock the owner out."""
import datetime
import os
from pathlib import Path

import numpy as np

HOME = Path.home() / ".forge"
PATH = HOME / "voiceprint.npz"
LOG = HOME / "voice.log"
SR = 16000
DEFAULT_NEED = 0.60  # for voiceprints enrolled before calibration existed
last = ""  # "0.71/0.60 1.6s": score / needed / seconds of speech, for debug lines
_encoder = None
_cache = None
_loaded = False


def _get_encoder():
    global _encoder
    if _encoder is None:
        from resemblyzer import VoiceEncoder
        _encoder = VoiceEncoder("cpu", verbose=False)
    return _encoder


def _speech(audio):
    from resemblyzer import preprocess_wav
    return preprocess_wav(np.asarray(audio, dtype="float32"), source_sr=SR)  # silence trimmed, normalised


def embed(audio):
    return _get_encoder().embed_utterance(_speech(audio))


def _cosine(a, b) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


def score(wav, mean) -> float:
    """Similarity of the best-matching 1.6 s piece of the speech (or the whole thing when shorter)."""
    enc = _get_encoder()
    if len(wav) < 1.6 * SR:
        return _cosine(enc.embed_utterance(wav), mean)
    full, partials, _ = enc.embed_utterance(wav, return_partials=True)
    return max([_cosine(full, mean)] + [_cosine(p, mean) for p in partials])


def save(embeddings, clips=None, max_need=None) -> float:
    """Store the mean voice, every enrollment embedding, and a threshold calibrated from 1.6 s pieces of the clips.
    max_need: never get stricter than this (when adding clips from a noisier room)."""
    global _cache, _loaded
    embs = np.stack([np.asarray(e, dtype="float32") for e in embeddings])
    mean = embs.mean(axis=0)
    need = DEFAULT_NEED
    if clips:
        enc = _get_encoder()
        pieces = []
        for c in clips:
            wav = _speech(c)
            if len(wav) >= 1.6 * SR:
                pieces += [_cosine(p, mean) for p in enc.embed_utterance(wav, return_partials=True)[1]]
            elif len(wav):
                pieces.append(_cosine(enc.embed_utterance(wav), mean))
        if pieces:  # noise lowers real scores; strangers stay near 0.5
            need = float(np.clip(np.percentile(pieces, 10) - 0.15, 0.55, 0.62))
    if max_need is not None:
        need = min(need, max_need)
    PATH.parent.mkdir(parents=True, exist_ok=True)
    np.savez(PATH, mean=mean, need=need, embs=embs)
    _cache, _loaded = (mean, need, embs), True
    return need


def load():
    global _cache, _loaded
    if not _loaded:
        _loaded = True
        try:
            with np.load(PATH) as d:
                embs = d["embs"] if "embs" in d else d["mean"][None]
                _cache = (d["mean"], float(d["need"]) if "need" in d else DEFAULT_NEED, embs)
        except Exception:
            _cache = None
    return _cache


def needed(seconds: float, trusted: bool = False) -> float:
    """Score a phrase must reach. Under 1 s of speech and inside a conversation each get a little slack."""
    need = load()[1]
    try:
        need = float(os.getenv("FORGE_VOICE_THRESH", "") or need)
    except ValueError:
        pass
    return need - (0.04 if seconds < 1.0 else 0.0) - (0.04 if trusted else 0.0)


def _log(line: str):
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a") as f:
            f.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {line}\n")
    except OSError:
        pass


def is_owner(audio, trusted: bool = False) -> bool:
    """True when the clip sounds like the enrolled owner. trusted: inside a conversation the owner already started."""
    global last
    data = load()
    if data is None:
        return True
    try:
        wav = _speech(audio)
        seconds = len(wav) / SR
        if seconds < 0.45:
            last = f"{seconds:.1f}s too short to check"
            _log(f"voice {last} -> {'ok (in conversation)' if trusted else 'ignored'}")
            return trusted
        s, need = score(wav, data[0]), needed(seconds, trusted)
        last = f"{s:.2f}/{need:.2f} {seconds:.1f}s"
        ok = s >= need
        _log(f"voice match {s:.2f} need {need:.2f} ({seconds:.1f}s{', in conversation' if trusted else ''}) -> "
             f"{'you' if ok else 'not you'}")
        return ok
    except Exception as e:
        last = "check failed"
        print("voice check skipped:", str(e)[:100])
        return True  # fail open: a broken check must never lock the owner out


def preload():
    try:
        _get_encoder()
        load()
    except Exception as e:
        print("voiceprint preload skipped:", str(e)[:100])
