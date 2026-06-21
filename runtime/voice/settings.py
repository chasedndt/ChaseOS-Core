"""Persisted voice settings (OpenCore).

A small JSON store at ``~/.chaseos/voice/voice-settings.json`` so the Voice settings card
actually takes effect across sessions. Adapters/pipeline read these via the ``effective_*``
helpers; an explicit environment variable always overrides the stored setting, and
``CHASEOS_VOICE_DISABLED`` remains a hard kill switch above everything.

Pure stdlib, fail-open: a missing/corrupt file yields defaults; this module imports nothing
from the voice adapters (so adapters can import it without a cycle).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, Any] = {
    "enabled": True,            # master on/off (CHASEOS_VOICE_DISABLED is a harder kill)
    "auto_speak": False,        # read assistant replies aloud
    "hands_free": False,        # continuous (VAD) conversation mode
    "stt_size": "base",         # whisper size or model dir
    "language": "",             # STT language hint ("" = auto-detect)
    "tts_voice": "",            # Piper voice name/path ("" = first installed)
    "speaking_rate": 1.0,       # TTS speed multiplier (0.5..2.0)
    "mic_mode": "push_to_talk", # push_to_talk | toggle
    "remember_consent": False,  # remember mic consent vs ask per session
    "silence_hold_seconds": 1.2,  # trailing pause that ends an utterance in hands-free
    "max_capture_seconds": 600.0,  # safety ceiling for open-ended notes (0 = unlimited)
}

_MIC_MODES = {"push_to_talk", "toggle"}


def voice_settings_path() -> Path:
    return Path.home() / ".chaseos" / "voice" / "voice-settings.json"


def _coerce_float(value: Any, default: float, lo: float, hi: float) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, out))


def validate_settings(raw: dict[str, Any]) -> dict[str, Any]:
    """Return a defaults-merged, type-coerced, clamped settings dict."""
    s = dict(DEFAULTS)
    if isinstance(raw, dict):
        for key in DEFAULTS:
            if key in raw and raw[key] is not None:
                s[key] = raw[key]
    s["enabled"] = bool(s["enabled"])
    s["auto_speak"] = bool(s["auto_speak"])
    s["hands_free"] = bool(s["hands_free"])
    s["remember_consent"] = bool(s["remember_consent"])
    s["stt_size"] = str(s["stt_size"] or "base").strip() or "base"
    s["language"] = str(s["language"] or "").strip()
    s["tts_voice"] = str(s["tts_voice"] or "").strip()
    s["speaking_rate"] = _coerce_float(s["speaking_rate"], 1.0, 0.5, 2.0)
    s["mic_mode"] = str(s["mic_mode"] or "").strip().lower()
    if s["mic_mode"] not in _MIC_MODES:
        s["mic_mode"] = "push_to_talk"
    s["silence_hold_seconds"] = _coerce_float(s["silence_hold_seconds"], 1.2, 0.4, 5.0)
    s["max_capture_seconds"] = _coerce_float(s["max_capture_seconds"], 600.0, 0.0, 7200.0)
    return s


def load_voice_settings() -> dict[str, Any]:
    """Load + validate stored settings. Fail-open to defaults."""
    path = voice_settings_path()
    if path.is_file():
        try:
            return validate_settings(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            return dict(DEFAULTS)
    return dict(DEFAULTS)


def save_voice_settings(patch: dict[str, Any]) -> dict[str, Any]:
    """Merge a patch of known keys into the stored settings (atomic write). Returns the result."""
    current = load_voice_settings()
    if isinstance(patch, dict):
        for key in DEFAULTS:
            if key in patch and patch[key] is not None:
                current[key] = patch[key]
    validated = validate_settings(current)
    path = voice_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="voice-settings-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(validated, handle, indent=2)
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
    return validated


# ── effective resolution (env override > stored setting > default) ────────────

def _env(*names: str) -> str:
    for name in names:
        value = str(os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


def effective_enabled() -> bool:
    """Whole-lane enable. CHASEOS_VOICE_DISABLED hard-off wins; else the stored setting."""
    if _env("CHASEOS_VOICE_DISABLED").lower() in {"1", "true", "yes", "on"}:
        return False
    return bool(load_voice_settings().get("enabled", True))


def effective_stt_size() -> str:
    return _env("CHASEOS_VOICE_STT_SIZE") or str(load_voice_settings().get("stt_size") or "base")


def effective_language() -> str:
    return _env("CHASEOS_VOICE_LANGUAGE") or str(load_voice_settings().get("language") or "")


def effective_tts_voice() -> str:
    return _env("CHASEOS_VOICE_PIPER_MODEL") or str(load_voice_settings().get("tts_voice") or "")


def effective_speaking_rate() -> float:
    raw = _env("CHASEOS_VOICE_SPEAKING_RATE")
    if raw:
        return _coerce_float(raw, 1.0, 0.5, 2.0)
    return _coerce_float(load_voice_settings().get("speaking_rate"), 1.0, 0.5, 2.0)
