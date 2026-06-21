"""Tests for the capture foundation: pure VAD decision + capture session lifecycle.

No microphone/hardware: the VAD core is pure, and the streaming functions are driven via a
fake sounddevice (InputStream that replays scripted frames into the callback).
"""

from __future__ import annotations

from pathlib import Path
import sys
import types
import wave

import pytest

VAULT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(VAULT))


# ── pure VAD decision ─────────────────────────────────────────────────────────

def test_vad_waits_for_speech_then_stops_on_trailing_silence():
    from runtime.voice.audio_io import vad_should_stop

    state: dict = {}
    # t=0 silence (no speech yet) -> keep waiting
    assert vad_should_stop(state, rms=0.0, now=0.0, silence_hold_seconds=1.0, start_timeout_seconds=8.0) is None
    # speech starts
    assert vad_should_stop(state, rms=0.05, now=0.5, silence_hold_seconds=1.0, start_timeout_seconds=8.0) is None
    assert state["speech_started"] is True
    # brief pause (< hold) -> keep
    assert vad_should_stop(state, rms=0.0, now=1.0, silence_hold_seconds=1.0, start_timeout_seconds=8.0) is None
    # trailing silence exceeds hold (1.0s since last voice at 0.5) -> stop
    assert vad_should_stop(state, rms=0.0, now=1.6, silence_hold_seconds=1.0, start_timeout_seconds=8.0) == "silence"


def test_vad_start_timeout_when_no_speech():
    from runtime.voice.audio_io import vad_should_stop

    state: dict = {}
    assert vad_should_stop(state, rms=0.0, now=0.0, start_timeout_seconds=2.0) is None
    assert vad_should_stop(state, rms=0.0, now=2.1, start_timeout_seconds=2.0) == "start_timeout"


def test_vad_no_fixed_cap_long_utterance_keeps_going():
    """A long utterance (well past any old 15s cap) keeps recording as long as speech continues."""
    from runtime.voice.audio_io import vad_should_stop

    state: dict = {}
    now = 0.0
    for _ in range(120):  # 120 * 0.5s = 60s of continuous speech
        now += 0.5
        assert vad_should_stop(state, rms=0.05, now=now, silence_hold_seconds=1.2, max_seconds=0) is None


def test_vad_safety_ceiling_stops_runaway():
    from runtime.voice.audio_io import vad_should_stop

    state: dict = {}
    assert vad_should_stop(state, rms=0.05, now=0.0, max_seconds=10.0) is None
    assert vad_should_stop(state, rms=0.05, now=11.0, max_seconds=10.0) == "max"


def test_block_rms_distinguishes_speech_from_silence():
    import numpy as np
    from runtime.voice.audio_io import block_rms

    silence = np.zeros(160, dtype=np.int16)
    loud = (np.ones(160, dtype=np.int16) * 8000)
    assert block_rms(silence) < 0.01
    assert block_rms(loud) > 0.1


# ── fake sounddevice for streaming wrappers ───────────────────────────────────

class _FakeInputStream:
    """Replays scripted int16 blocks into the callback on start/enter."""

    scripted_blocks: list = []  # set per-test

    def __init__(self, *, samplerate, channels, dtype, callback, **kw):
        self._callback = callback

    def _feed(self):
        import numpy as np
        for block in type(self).scripted_blocks:
            arr = np.asarray(block, dtype=np.int16).reshape(-1, 1)
            try:
                self._callback(arr, len(arr), None, None)
            except Exception:
                break

    # context-manager form (capture_until_silence)
    def __enter__(self):
        self._feed()
        return self

    def __exit__(self, *exc):
        return False

    # start/stop form (start_capture/stop_capture)
    def start(self):
        self._feed()

    def stop(self):
        pass

    def close(self):
        pass


def _install_fake_sd(monkeypatch, blocks):
    import numpy as np  # ensure numpy import inside functions resolves
    fake = types.ModuleType("sounddevice")
    _FakeInputStream.scripted_blocks = blocks

    class _CallbackStop(Exception):
        pass

    fake.InputStream = _FakeInputStream
    fake.CallbackStop = _CallbackStop
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    monkeypatch.setitem(sys.modules, "numpy", np)


def _loud(n=320):
    import numpy as np
    return (np.ones(n, dtype=np.int16) * 9000)


def _quiet(n=320):
    import numpy as np
    return np.zeros(n, dtype=np.int16)


def test_capture_until_silence_requires_consent(monkeypatch):
    from runtime.voice import audio_io
    monkeypatch.setattr(audio_io, "capture_backend", lambda: "sounddevice")
    r = audio_io.capture_until_silence(consent=False)
    assert r.ok is False and r.reason == "no_consent"
    assert r.authority.microphone_capture_performed is False


def test_capture_until_silence_records_speech_then_silence(monkeypatch, tmp_path):
    from runtime.voice import audio_io

    monkeypatch.setattr(audio_io, "capture_backend", lambda: "sounddevice")
    # speech blocks then silent blocks; with fast monotonic the VAD silence-hold triggers.
    _install_fake_sd(monkeypatch, [_loud(), _loud(), _quiet(), _quiet(), _quiet(), _quiet()])
    # make time advance deterministically per call
    ticks = iter([float(i) * 0.5 for i in range(50)])
    monkeypatch.setattr(audio_io.time, "monotonic", lambda: next(ticks))
    out = tmp_path / "utt.wav"
    r = audio_io.capture_until_silence(consent=True, output_path=str(out),
                                       silence_hold_seconds=1.0, start_timeout_seconds=8.0, samplerate=16000)
    assert r.ok is True
    assert r.speech_detected is True
    assert r.reason == "silence"
    assert Path(r.audio_path).is_file()
    assert r.authority.microphone_capture_performed and r.authority.audio_file_written


def test_capture_until_silence_no_speech_blocks(monkeypatch, tmp_path):
    from runtime.voice import audio_io

    monkeypatch.setattr(audio_io, "capture_backend", lambda: "sounddevice")
    _install_fake_sd(monkeypatch, [_quiet(), _quiet(), _quiet()])
    ticks = iter([float(i) * 5.0 for i in range(20)])  # advance fast past start_timeout
    monkeypatch.setattr(audio_io.time, "monotonic", lambda: next(ticks))
    r = audio_io.capture_until_silence(consent=True, output_path=str(tmp_path / "none.wav"),
                                       start_timeout_seconds=2.0)
    assert r.ok is False
    assert r.speech_detected is False
    assert r.reason == "start_timeout"


def test_start_stop_capture_session_records_any_length(monkeypatch, tmp_path):
    from runtime.voice import audio_io

    monkeypatch.setattr(audio_io, "capture_backend", lambda: "sounddevice")
    _install_fake_sd(monkeypatch, [_loud(), _loud(), _loud()])
    out = tmp_path / "note.wav"
    started = audio_io.start_capture(consent=True, output_path=str(out), samplerate=16000)
    assert started.ok is True and started.session_id
    assert audio_io.active_capture_session_count() == 1
    stopped = audio_io.stop_capture(started.session_id)
    assert stopped.ok is True
    assert stopped.reason == "stopped"
    assert Path(stopped.audio_path).is_file()
    assert audio_io.active_capture_session_count() == 0
    # stopping an unknown session is honest-blocked
    assert audio_io.stop_capture("nope").ok is False


def test_start_capture_requires_consent(monkeypatch):
    from runtime.voice import audio_io
    monkeypatch.setattr(audio_io, "capture_backend", lambda: "sounddevice")
    assert audio_io.start_capture(consent=False).ok is False
