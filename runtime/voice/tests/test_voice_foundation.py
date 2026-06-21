"""Foundation + open-source live-adapter tests for the ChaseOS voice subsystem (Item E).

Locks two things:
1. The no-effect guarantee: when an engine is absent, the lane reports an honest
   blocked state and sets no authority flags.
2. The open-source live adapters (faster-whisper STT, Piper TTS) do real work when
   their engine is present — verified with mocked engines so the heavy deps are
   not required to run the suite.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

VAULT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(VAULT))


@pytest.fixture(autouse=True)
def _clean_voice_env(monkeypatch):
    for var in (
        "CHASEOS_VOICE_DISABLED", "CHASEOS_VOICE_STT_PROVIDER", "CHASEOS_VOICE_TTS_PROVIDER",
        "CHASEOS_VOICE_PIPER_BIN", "CHASEOS_VOICE_PIPER_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)
    yield


def _force_engines_absent(monkeypatch):
    """Make both open-source engines look uninstalled, deterministically."""
    from runtime.voice.adapters import local_whisper, piper_local
    monkeypatch.setattr(local_whisper, "_faster_whisper_available", lambda: False)
    monkeypatch.setattr(piper_local, "_piper_binary", lambda: "")


# ── defaults + honesty ───────────────────────────────────────────────────────

def test_default_adapters_are_open_source_local():
    from runtime.voice.provider_registry import resolve_stt_adapter, resolve_tts_adapter

    assert resolve_stt_adapter().provider_id == "local-whisper"
    assert resolve_tts_adapter().provider_id == "piper-local"


def test_blocked_when_engines_absent_sets_no_authority(monkeypatch):
    _force_engines_absent(monkeypatch)
    from runtime.voice.models import STTRequest, TTSRequest
    from runtime.voice.provider_registry import resolve_stt_adapter, resolve_tts_adapter

    stt = resolve_stt_adapter().transcribe(STTRequest(audio_ref="07_LOGS/x.wav"))
    tts = resolve_tts_adapter().synthesize(TTSRequest(text="hello"))
    assert stt.ok is False and stt.transcript == "" and stt.blocked_reason
    assert tts.ok is False and tts.audio_path is None and tts.audio_bytes == 0 and tts.blocked_reason
    assert any(stt.authority.to_dict().values()) is False
    assert any(tts.authority.to_dict().values()) is False


def test_unknown_configured_provider_falls_back_to_null(monkeypatch):
    monkeypatch.setenv("CHASEOS_VOICE_STT_PROVIDER", "some-unregistered-stt")
    monkeypatch.setenv("CHASEOS_VOICE_TTS_PROVIDER", "some-unregistered-tts")
    from runtime.voice.provider_registry import resolve_stt_adapter, resolve_tts_adapter

    stt = resolve_stt_adapter()
    tts = resolve_tts_adapter()
    assert stt.provider_id == "null"
    assert stt.readiness()["intended_provider"] == "some-unregistered-stt"
    assert tts.readiness()["intended_provider"] == "some-unregistered-tts"


def test_voice_lane_disabled_switch(monkeypatch):
    monkeypatch.setenv("CHASEOS_VOICE_DISABLED", "1")
    from runtime.voice.readiness import voice_delivery_truth

    truth = voice_delivery_truth(probe=False)
    assert truth["transport_enabled"] is False
    assert "disabled" in (truth["blocked_reason"] or "").lower()


def test_voice_delivery_truth_honest_when_engines_absent(monkeypatch):
    _force_engines_absent(monkeypatch)
    from runtime.voice.readiness import voice_delivery_truth

    truth = voice_delivery_truth(probe=False)
    assert truth["surface"] == "voice_delivery_truth"
    assert truth["stt_provider"] == "local-whisper"
    assert truth["tts_provider"] == "piper-local"
    assert truth["can_transcribe"] is False
    assert truth["can_synthesize"] is False
    assert truth["blocked_reason"]
    assert set(truth["authority"].values()) == {False}
    # The open-source local engines are the wired/implemented providers.
    impl = {p["provider_id"] for p in truth["providers"] if p["implemented"]}
    assert {"local-whisper", "piper-local"} <= impl


def test_list_voice_providers_filter():
    from runtime.voice.provider_registry import list_voice_providers

    assert {p["kind"] for p in list_voice_providers("stt")} == {"stt"}
    assert {p["kind"] for p in list_voice_providers("tts")} == {"tts"}
    assert len(list_voice_providers()) >= 4


# ── open-source live adapters (mocked engines) ───────────────────────────────

def test_local_whisper_transcribes_when_engine_present(monkeypatch, tmp_path):
    from runtime.voice.adapters import local_whisper
    from runtime.voice.adapters.local_whisper import LocalWhisperSTTAdapter
    from runtime.voice.models import STTRequest

    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"RIFF....WAVE")  # presence only; the engine is mocked

    class _Seg:
        def __init__(self, text):
            self.text = text

    class _FakeModel:
        def transcribe(self, path, **kwargs):
            assert Path(path) == audio
            return ([_Seg("Hello "), _Seg("world.")], {"language": "en"})

    monkeypatch.setattr(local_whisper, "_faster_whisper_available", lambda: True)
    monkeypatch.setattr(LocalWhisperSTTAdapter, "_get_model", lambda self: _FakeModel())

    result = LocalWhisperSTTAdapter().transcribe(STTRequest(audio_ref=str(audio)))
    assert result.ok is True
    assert result.transcript == "Hello world."
    assert result.provider_id == "local-whisper"
    assert result.authority.stt_provider_called is True
    assert result.authority.audio_file_written is False  # STT writes nothing


def test_local_whisper_blocks_on_missing_audio(monkeypatch):
    from runtime.voice.adapters import local_whisper
    from runtime.voice.adapters.local_whisper import LocalWhisperSTTAdapter
    from runtime.voice.models import STTRequest

    monkeypatch.setattr(local_whisper, "_faster_whisper_available", lambda: True)
    result = LocalWhisperSTTAdapter().transcribe(STTRequest(audio_ref="does/not/exist.wav"))
    assert result.ok is False
    assert "not found" in (result.blocked_reason or "").lower()
    assert any(result.authority.to_dict().values()) is False


def test_piper_synthesizes_when_engine_present(monkeypatch, tmp_path):
    from runtime.voice.adapters import piper_local
    from runtime.voice.adapters.piper_local import PiperLocalTTSAdapter
    from runtime.voice.models import TTSRequest

    model = tmp_path / "voice.onnx"
    model.write_bytes(b"onnx")
    out = tmp_path / "out.wav"

    monkeypatch.setattr(piper_local, "_piper_binary", lambda: "piper")
    monkeypatch.setattr(piper_local, "_piper_model", lambda: str(model))

    def fake_run(cmd, **kwargs):
        # piper writes the --output_file; simulate that.
        out_idx = cmd.index("--output_file") + 1
        Path(cmd[out_idx]).write_bytes(b"\x00" * 2048)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(piper_local.subprocess, "run", fake_run)

    result = PiperLocalTTSAdapter().synthesize(TTSRequest(text="hello there", output_path=str(out)))
    assert result.ok is True
    assert result.audio_path == str(out)
    assert result.audio_bytes == 2048
    assert result.provider_id == "piper-local"
    assert result.authority.tts_provider_called is True
    assert result.authority.audio_file_written is True


def test_piper_blocks_without_model(monkeypatch):
    from runtime.voice.adapters import piper_local
    from runtime.voice.adapters.piper_local import PiperLocalTTSAdapter
    from runtime.voice.models import TTSRequest

    monkeypatch.setattr(piper_local, "_piper_binary", lambda: "piper")
    monkeypatch.setattr(piper_local, "_piper_model", lambda: "")  # no model configured
    result = PiperLocalTTSAdapter().synthesize(TTSRequest(text="hi"))
    assert result.ok is False
    assert "model" in (result.blocked_reason or "").lower()
    assert any(result.authority.to_dict().values()) is False
