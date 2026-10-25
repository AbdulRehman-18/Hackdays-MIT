"""Listening rules ported from Monday (AGENTS.md 12.4). No real microphone: a fake InputStream feeds synthetic audio."""
import shutil
import subprocess
import sys
import time

import numpy as np
import pytest

from forge.assistant import voice

SR = voice.SR


class FakeMouth:
    def __init__(self, recent="", asked=False):
        self.speaking, self.mute_until, self._recent, self._asked, self.said = False, 0.0, recent, asked, []

    def is_echo(self, heard):
        return voice._is_echo(heard, self._recent)

    def strip_echo(self, heard):
        return voice._strip_echo(heard, self._recent)

    def asked(self):
        return self._asked

    def say(self, text):
        self.said.append(text)


def make(busy=False, mouth=None):
    cmds, wakes = [], []
    lis = voice.Listener("Forge", cmds.append, lambda: wakes.append(1), lambda v: None, lambda: busy,
                         mouth or FakeMouth())
    return lis, cmds, wakes


# ---- echo ----
def test_echo_with_number_words():
    said = "Timer set for 5 minutes."
    assert voice._is_echo("five minutes", said)
    assert not voice._is_echo("ten minutes", said)  # a different number is really the user
    assert not voice._is_echo("open youtube and play lofi", said)


def test_strip_echo_keeps_the_user_part():
    said = "Here is the weather in London today"
    assert voice._strip_echo("the weather in London today stop", said) == "stop"
    assert voice._strip_echo("open safari", "") == "open safari"


# ---- wake word ----
def test_wake_phrases_and_whisper_spellings():
    w = voice.wake_pattern("Forge")
    assert w.match("Hey Forge, open YouTube").group(1) == "open YouTube"
    assert w.match("okay hey forj what's the time").group(1) == "what's the time"
    assert w.match("Forge?").group(1) == ""
    assert w.match("Hey Ford, play music")
    assert not w.match("I went to the forge yesterday")


def test_needs_the_wake_word_when_idle():
    lis, cmds, wakes = make()
    lis._route("open YouTube and play lofi")
    assert cmds == [] and wakes == []
    lis._route("Hey Forge, open YouTube and play lofi")
    assert cmds == ["open YouTube and play lofi"]


def test_wake_word_alone_wakes_then_next_phrase_is_the_command():
    lis, cmds, wakes = make()
    lis._route("Hey Forge")
    assert wakes == [1] and cmds == []
    lis._route("what's the weather")
    assert cmds == ["what's the weather"]


def test_conversation_window_needs_no_wake_word():
    lis, cmds, _ = make()
    lis.converse(8)
    lis._route("and tomorrow?")
    assert cmds == ["and tomorrow?"]


# ---- noise can't interrupt ----
def test_two_real_words_rule_while_busy():
    lis, cmds, _ = make(busy=True)
    lis._route("yeah")  # a filler
    lis._route("hmm okay")
    assert cmds == []
    lis._route("stop")  # stop words may interrupt alone
    lis._route("open spotify instead")
    assert cmds == ["stop", "open spotify instead"]


def test_bare_yes_answers_a_question():
    lis, cmds, _ = make(busy=True, mouth=FakeMouth(asked=True))
    lis._route("yes")
    assert cmds == ["yes"]
    lis2, cmds2, _ = make(busy=True)
    lis2.expect(8)
    lis2._route("nope")
    assert cmds2 == ["nope"]


def test_loop_guard_ignores_a_repeat_without_the_wake_word():
    lis, cmds, _ = make()
    lis.converse(8)
    lis._route("set a timer for five minutes")
    lis.converse(8)
    lis._route("set a timer for 5 minutes")  # same command (number words normalised)
    assert cmds == ["set a timer for five minutes"]
    lis._route("Hey Forge, set a timer for 5 minutes")  # with the wake word it goes through
    assert len(cmds) == 2


# ---- unfinished sentences ----
def test_dangling_sentences():
    for t in ("I want you to", "open the", "can you", "play something and"):
        assert voice.DANGLING.search(t), t
    for t in ("open the browser", "what's the time", "play lofi"):
        assert not voice.DANGLING.search(t), t


def test_unfinished_sentence_waits_for_the_rest():
    lis, cmds, _ = make()
    lis._text("Hey Forge, I want you to")
    assert cmds == [] and lis.pending
    lis._text("open YouTube")
    assert cmds == ["I want you to open YouTube"]


def test_unfinished_sentence_is_sent_after_finish_s():
    lis, cmds, _ = make()
    lis._text("Hey Forge, open the")
    lis.pending = (lis.pending[0], time.time() - voice.FINISH_S - 0.1)
    lis._flush_pending()
    assert cmds == ["open the"]


def test_echo_is_dropped_before_routing():
    lis, cmds, _ = make(mouth=FakeMouth(recent="Hey Forge is ready, what's the time"))
    lis.converse(8)
    lis._text("what's the time")
    assert cmds == []


# ---- clap ----
def test_double_clap():
    lv = np.full(40, 0.003)
    lv[5:7] = 0.3
    lv[15:17] = 0.3
    assert voice._double_clap(lv, 0.003)
    lv2 = np.full(40, 0.003)
    lv2[5:25] = 0.3  # a long sound is not claps
    assert not voice._double_clap(lv2, 0.003)


# ---- denoise ----
def test_denoise_keeps_shape_and_dtype():
    x = (np.sin(np.linspace(0, 600, SR * 3)) * 0.1).astype("float32")
    y = voice.denoise(x)
    assert y.shape == x.shape and y.dtype == x.dtype


def test_denoise_falls_back_when_noisereduce_is_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "noisereduce", None)
    x = np.ones(SR, "float32") * 0.01
    assert voice.denoise(x) is x


# ---- the engine on a synthetic noisy stream ----
def _fan(seconds, db, seed=0):
    rng = np.random.default_rng(seed)
    n = rng.normal(0, 1, int(SR * seconds))
    n = np.convolve(n, np.ones(8) / 8, mode="same")  # fan hum: low-passed noise
    return (n / np.sqrt(np.mean(n ** 2)) * 10 ** (db / 20)).astype("float32")


def _speech(text, tmp_path):
    out = tmp_path / "s.wav"
    subprocess.run(["say", "-o", str(out), "--data-format=LEI16@16000", text], check=True)
    import soundfile as sf
    a, _ = sf.read(out, dtype="float32")
    return (a / np.sqrt(np.mean(a ** 2) + 1e-9) * 0.08).astype("float32")


def _run_engine(monkeypatch, audio, transcript, busy=False):
    """Feed audio through Listener._engine with a fake mic. The fake Whisper hears transcript in a clip that holds
    speech (well above the fan) and nothing in a clip of room noise, as real Whisper's unsure-piece filter does."""
    import sounddevice as sd
    clips = []

    class FakeStream:
        def __init__(self, samplerate, channels, dtype, blocksize, callback):
            self.cb, self.n = callback, blocksize

        def start(self):
            for i in range(0, len(audio) - self.n, self.n):
                self.cb(audio[i:i + self.n, None], self.n, None, None)

        def stop(self):
            pass

        def close(self):
            pass
    monkeypatch.setattr(sd, "InputStream", FakeStream)
    def whisper(clip, **kw):
        clips.append(len(clip) / SR)
        return transcript if float(np.sqrt(np.mean(clip ** 2))) > 0.045 else ""
    monkeypatch.setattr(voice, "_transcribe", whisper)
    monkeypatch.setattr(voice, "CLAP", False)
    lis, cmds, wakes = make(busy=busy)
    lis.stop_ev.set()  # handle every buffered frame, then return
    lis._engine()
    return clips, cmds, wakes


@pytest.mark.skipif(not shutil.which("say"), reason="needs macOS say for synthetic speech")
@pytest.mark.parametrize("fan_db", [-36, -30])
def test_engine_routes_a_command_in_a_noisy_room(monkeypatch, tmp_path, fan_db):
    sp = _speech("Hey Forge, what's the weather in London", tmp_path)
    audio = _fan(5 + len(sp) / SR, fan_db)
    audio[int(2 * SR):int(2 * SR) + len(sp)] += sp
    clips, cmds, _ = _run_engine(monkeypatch, audio, "Hey Forge, what's the weather in London")
    assert cmds == ["what's the weather in London"]
    assert max(clips) < len(sp) / SR + 1.0  # only the spoken part went to Whisper, not the room around it


@pytest.mark.parametrize("fan_db", [-36, -30])
def test_engine_ignores_pure_noise(monkeypatch, fan_db):
    clips, cmds, wakes = _run_engine(monkeypatch, _fan(6, fan_db, seed=3), "eleven")
    assert cmds == [] and wakes == []
    assert len(clips) <= 1  # at most one blip while the room's noise is still being learned


@pytest.mark.skipif(not shutil.which("say"), reason="needs macOS say for synthetic speech")
def test_engine_ignores_a_stranger_without_the_wake_word(monkeypatch, tmp_path):
    sp = _speech("I'm sorry, I'm sorry, see you next time", tmp_path)
    audio = _fan(4 + len(sp) / SR, -36)
    audio[SR:SR + len(sp)] += sp
    clips, cmds, _ = _run_engine(monkeypatch, audio, "I'm sorry, I'm sorry, see you next time")
    assert clips and cmds == []


# ---- voice lock ----
def test_voice_lock_close_passes_random_fails_and_missing_fails_open(monkeypatch, tmp_path):
    from forge.assistant import voiceprint as vp
    monkeypatch.setattr(vp, "PATH", tmp_path / "voiceprint.npz")
    monkeypatch.setattr(vp, "LOG", tmp_path / "voice.log")
    monkeypatch.setattr(vp, "_loaded", False)
    rng = np.random.default_rng(0)
    me = rng.normal(0, 1, 256).astype("float32")
    vp.save([me + rng.normal(0, 0.05, 256) for _ in range(5)])
    monkeypatch.setattr(vp, "_speech", lambda a: np.asarray(a))
    monkeypatch.setattr(vp, "score", lambda wav, mean: vp._cosine(wav[:256], mean))
    close = np.concatenate([me + rng.normal(0, 0.05, 256), np.zeros(SR * 2)]).astype("float32")
    stranger = np.concatenate([rng.normal(0, 1, 256), np.zeros(SR * 2)]).astype("float32")
    assert vp.is_owner(close) and not vp.is_owner(stranger)

    def broken(a):
        raise ImportError("no resemblyzer")
    monkeypatch.setattr(vp, "_speech", broken)
    assert vp.is_owner(stranger)  # fail open: a broken check never locks the owner out


# ---- speech text ----
def test_sentence_splitter_and_clean_for_speech():
    sp = voice.SentenceSplitter()
    assert sp.feed("Hello there, friend. How are") == ["Hello there, friend."]
    assert sp.flush() == ["How are"]
    assert voice.clean_for_speech("**Bold** [link](http://x.y) `code`") == "Bold link code"


@pytest.mark.skipif(not (shutil.which("say") and __import__("os").getenv("FORGE_SLOW_TESTS")),
                    reason="real Whisper: set FORGE_SLOW_TESTS=1")
def test_real_whisper_hears_the_command(tmp_path):
    sp = _speech("Hey Forge, open YouTube and play lofi", tmp_path)
    text = voice._transcribe(sp, prompt=voice.VOCAB, lock=False)
    assert voice.wake_pattern().match(text)
