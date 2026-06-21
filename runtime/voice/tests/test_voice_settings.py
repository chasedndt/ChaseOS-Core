"""Tests for the persisted voice settings store + adapters honoring it."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

VAULT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(VAULT))


@pytest.fixture()
def settings_in_tmp(monkeypatch, tmp_path):
    from runtime.voice import settings as vs

    target = tmp_path / "voice-settings.json"
    monkeypatch.setattr(vs, "voice_settings_path", lambda: target)
    for var in ("CHASEOS_VOICE_DISABLED", "CHASEOS_VOICE_STT_SIZE", "CHASEOS_VOICE_LANGUAGE",
                "CHASEOS_VOICE_PIPER_MODEL", "CHASEOS_VOICE_SPEAKING_RATE", "CHASEOS_VOICE_WHISPER_MODEL"):
        monkeypatch.delenv(var, raising=False)
    return vs


def test_defaults_when_no_file(settings_in_tmp):
    s = settings_in_tmp.load_voice_settings()
    assert s["enabled"] is True
    assert s["auto_speak"] is False
    assert s["hands_free"] is False
    assert s["stt_size"] == "base"
    assert s["mic_mode"] == "push_to_talk"
    assert s["speaking_rate"] == 1.0


def test_save_and_reload_roundtrip(settings_in_tmp):
    saved = settings_in_tmp.save_voice_settings({"auto_speak": True, "hands_free": True, "tts_voice": "en_US-lessac-medium"})
    assert saved["auto_speak"] is True and saved["hands_free"] is True
    reloaded = settings_in_tmp.load_voice_settings()
    assert reloaded["auto_speak"] is True
    assert reloaded["tts_voice"] == "en_US-lessac-medium"


def test_validation_clamps_and_coerces(settings_in_tmp):
    saved = settings_in_tmp.save_voice_settings({
        "speaking_rate": 9.0,        # clamp to 2.0
        "silence_hold_seconds": 0.0,  # clamp to 0.4
        "mic_mode": "nonsense",       # -> push_to_talk
        "max_capture_seconds": -5,    # clamp to 0
    })
    assert saved["speaking_rate"] == 2.0
    assert saved["silence_hold_seconds"] == 0.4
    assert saved["mic_mode"] == "push_to_talk"
    assert saved["max_capture_seconds"] == 0.0


def test_corrupt_file_fails_open(settings_in_tmp):
    settings_in_tmp.voice_settings_path().parent.mkdir(parents=True, exist_ok=True)
    settings_in_tmp.voice_settings_path().write_text("{not json", encoding="utf-8")
    s = settings_in_tmp.load_voice_settings()
    assert s == settings_in_tmp.load_voice_settings()  # stable
    assert s["enabled"] is True  # defaults


def test_effective_env_overrides_stored(settings_in_tmp, monkeypatch):
    settings_in_tmp.save_voice_settings({"stt_size": "small", "speaking_rate": 1.5})
    assert settings_in_tmp.effective_stt_size() == "small"
    assert settings_in_tmp.effective_speaking_rate() == 1.5
    monkeypatch.setenv("CHASEOS_VOICE_STT_SIZE", "tiny")
    assert settings_in_tmp.effective_stt_size() == "tiny"  # env wins


def test_disabled_master_setting_disables_lane(settings_in_tmp, monkeypatch):
    from runtime.voice import provider_registry as reg

    settings_in_tmp.save_voice_settings({"enabled": False})
    assert reg.voice_lane_enabled() is False
    # hard env kill also works regardless of stored value
    settings_in_tmp.save_voice_settings({"enabled": True})
    assert reg.voice_lane_enabled() is True
    monkeypatch.setenv("CHASEOS_VOICE_DISABLED", "1")
    assert reg.voice_lane_enabled() is False


def test_stt_adapter_uses_stored_size(settings_in_tmp, monkeypatch):
    from runtime.voice.adapters import local_whisper as lw

    settings_in_tmp.save_voice_settings({"stt_size": "small"})
    # No explicit CHASEOS_VOICE_WHISPER_MODEL → falls back to stored stt_size.
    assert lw._model_name() == "small"
    monkeypatch.setenv("CHASEOS_VOICE_WHISPER_MODEL", "base")
    assert lw._model_name() == "base"  # explicit override wins


def test_tts_adapter_resolves_voice_by_name(settings_in_tmp, monkeypatch, tmp_path):
    from runtime.voice.adapters import piper_local as pl

    voice_dir = tmp_path / "piper"
    voice_dir.mkdir(parents=True)
    (voice_dir / "en_US-amy-low.onnx").write_bytes(b"x")
    (voice_dir / "en_US-lessac-medium.onnx").write_bytes(b"x")
    monkeypatch.setattr(pl, "_voice_dirs", lambda: [voice_dir])
    settings_in_tmp.save_voice_settings({"tts_voice": "en_US-lessac-medium"})
    assert pl._piper_model() == str(voice_dir / "en_US-lessac-medium.onnx")
    assert set(pl.list_piper_voices()) == {"en_US-amy-low", "en_US-lessac-medium"}
