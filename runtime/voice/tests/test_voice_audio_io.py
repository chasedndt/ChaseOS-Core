"""Tests for the voice audio I/O layer + end-to-end pipelines (mic + playback).

Backends are mocked so the suite runs without sounddevice/numpy or audio hardware.
Locks the privacy gate (no recording without consent) and the honest authority flags.
"""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

VAULT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(VAULT))


# ── capture: privacy + honesty ───────────────────────────────────────────────

def test_capture_requires_consent(monkeypatch):
    from runtime.voice import audio_io
    monkeypatch.setattr(audio_io, "capture_backend", lambda: "sounddevice")
    result = audio_io.capture_microphone(seconds=1, consent=False)
    assert result.ok is False
    assert "consent" in (result.blocked_reason or "").lower()
    assert result.authority.microphone_capture_performed is False


def test_capture_blocked_without_backend(monkeypatch):
    from runtime.voice import audio_io
    monkeypatch.setattr(audio_io, "capture_backend", lambda: "")
    result = audio_io.capture_microphone(seconds=1, consent=True)
    assert result.ok is False
    assert result.blocked_reason
    assert result.authority.microphone_capture_performed is False


def test_capture_records_when_backend_present(monkeypatch, tmp_path):
    from runtime.voice import audio_io

    out = tmp_path / "mic.wav"
    monkeypatch.setattr(audio_io, "capture_backend", lambda: "sounddevice")

    # Stub the sounddevice + numpy + wave path by replacing the function body's deps:
    # simplest is to write a valid wav ourselves via the real wave module, which the
    # function uses — but the recording array comes from sounddevice. Patch import.
    import types

    fake_sd = types.SimpleNamespace(
        rec=lambda frames, samplerate, channels, dtype: _FakeArray(frames),
        wait=lambda: None,
    )
    fake_np = types.ModuleType("numpy")
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    monkeypatch.setitem(sys.modules, "numpy", fake_np)

    result = audio_io.capture_microphone(seconds=1, output_path=str(out), samplerate=8000, consent=True)
    assert result.ok is True
    assert result.audio_path == str(out)
    assert out.is_file() and out.stat().st_size > 0
    assert result.backend == "sounddevice"
    assert result.authority.microphone_capture_performed is True
    assert result.authority.audio_file_written is True


class _FakeArray:
    """Minimal stand-in for the numpy int16 recording array."""

    def __init__(self, frames):
        self._frames = frames

    def tobytes(self):
        return b"\x00\x01" * self._frames


# ── playback ─────────────────────────────────────────────────────────────────

def test_playback_blocked_without_backend(monkeypatch, tmp_path):
    from runtime.voice import audio_io
    monkeypatch.setattr(audio_io, "playback_backend", lambda: "")
    wav = tmp_path / "x.wav"
    wav.write_bytes(b"RIFF")
    result = audio_io.play_audio(str(wav))
    assert result.ok is False
    assert result.authority.audio_played is False


def test_playback_missing_file(monkeypatch):
    from runtime.voice import audio_io
    monkeypatch.setattr(audio_io, "playback_backend", lambda: "aplay")
    result = audio_io.play_audio("nope.wav")
    assert result.ok is False
    assert "not found" in (result.blocked_reason or "").lower()


def test_playback_runs_with_cli_backend(monkeypatch, tmp_path):
    from runtime.voice import audio_io
    wav = tmp_path / "ok.wav"
    wav.write_bytes(b"RIFF....WAVE")
    monkeypatch.setattr(audio_io, "playback_backend", lambda: "aplay")
    calls = {}
    monkeypatch.setattr(audio_io.subprocess, "run", lambda cmd, **kw: calls.setdefault("cmd", cmd))
    result = audio_io.play_audio(str(wav))
    assert result.ok is True
    assert result.authority.audio_played is True
    assert calls["cmd"][0] == "aplay"


def test_audio_io_readiness_shape():
    from runtime.voice.audio_io import audio_io_readiness
    r = audio_io_readiness()
    assert set(["capture_backend", "playback_backend", "can_capture", "can_play",
                "consent_required_for_capture"]).issubset(r.keys())
    assert r["consent_required_for_capture"] is True


# ── end-to-end pipeline ──────────────────────────────────────────────────────

def test_transcribe_microphone_stops_at_capture_without_consent():
    from runtime.voice.pipeline import transcribe_microphone
    out = transcribe_microphone(seconds=1, consent=False)
    assert out["ok"] is False
    assert out["stage"] == "capture"
    assert set(out["authority"].values()) == {False}


def test_transcribe_microphone_full_chain(monkeypatch, tmp_path):
    from runtime.voice import pipeline
    from runtime.voice.models import AudioCaptureResult, STTResult, VoiceAuthority

    clip = tmp_path / "rec.wav"
    clip.write_bytes(b"RIFF")
    monkeypatch.setattr(pipeline, "capture_microphone",
                        lambda **kw: AudioCaptureResult(ok=True, audio_path=str(clip), seconds=1, samplerate=16000,
                                                        backend="sounddevice",
                                                        authority=VoiceAuthority(microphone_capture_performed=True,
                                                                                 audio_file_written=True)))

    class _STT:
        def transcribe(self, req):
            assert req.audio_ref == str(clip)
            return STTResult(ok=True, transcript="hello world", provider_id="local-whisper",
                             authority=VoiceAuthority(stt_provider_called=True))

    monkeypatch.setattr(pipeline, "resolve_stt_adapter", lambda: _STT())
    out = pipeline.transcribe_microphone(seconds=1, consent=True)
    assert out["ok"] is True
    assert out["transcript"] == "hello world"
    auth = out["authority"]
    assert auth["microphone_capture_performed"] and auth["audio_file_written"] and auth["stt_provider_called"]


def test_speak_text_full_chain(monkeypatch, tmp_path):
    from runtime.voice import pipeline
    from runtime.voice.models import AudioPlaybackResult, TTSResult, VoiceAuthority

    wav = tmp_path / "say.wav"
    wav.write_bytes(b"RIFF")

    class _TTS:
        def synthesize(self, req):
            return TTSResult(ok=True, audio_path=str(wav), audio_bytes=4, provider_id="piper-local",
                             authority=VoiceAuthority(tts_provider_called=True, audio_file_written=True))

    monkeypatch.setattr(pipeline, "resolve_tts_adapter", lambda: _TTS())
    monkeypatch.setattr(pipeline, "play_audio",
                        lambda p: AudioPlaybackResult(ok=True, audio_path=p, backend="winsound",
                                                      authority=VoiceAuthority(audio_played=True)))
    out = pipeline.speak_text("hello", play=True)
    assert out["ok"] is True and out["played"] is True
    auth = out["authority"]
    assert auth["tts_provider_called"] and auth["audio_file_written"] and auth["audio_played"]


def test_speak_text_synth_only(monkeypatch, tmp_path):
    from runtime.voice import pipeline
    from runtime.voice.models import TTSResult, VoiceAuthority

    wav = tmp_path / "say.wav"
    wav.write_bytes(b"RIFF")

    class _TTS:
        def synthesize(self, req):
            return TTSResult(ok=True, audio_path=str(wav), provider_id="piper-local",
                             authority=VoiceAuthority(tts_provider_called=True, audio_file_written=True))

    monkeypatch.setattr(pipeline, "resolve_tts_adapter", lambda: _TTS())
    # play_audio must not be called when play=False
    monkeypatch.setattr(pipeline, "play_audio", lambda p: (_ for _ in ()).throw(AssertionError("should not play")))
    out = pipeline.speak_text("hello", play=False)
    assert out["ok"] is True and out["played"] is False
    assert out["authority"]["audio_played"] is False
