"""Voice provider registry + adapter resolution (provider-agnostic).

Declares a non-exhaustive set of voice providers (declaration is NOT capability)
and resolves an STT/TTS adapter from operator config. No provider is hardcoded as
the only option; the active provider comes from env. Until a live adapter is
implemented and registered, resolution returns the honest null adapter — never a
fabricated live path.

Env:
- ``CHASEOS_VOICE_STT_PROVIDER`` — preferred STT provider id (default: none → null)
- ``CHASEOS_VOICE_TTS_PROVIDER`` — preferred TTS provider id (default: none → null)
- ``CHASEOS_VOICE_DISABLED`` — hard off-switch for the whole lane
"""

from __future__ import annotations

import os

from runtime.voice.adapters.base import STTAdapter, TTSAdapter
from runtime.voice.adapters.local_whisper import LocalWhisperSTTAdapter
from runtime.voice.adapters.null_adapter import NullSTTAdapter, NullTTSAdapter
from runtime.voice.adapters.piper_local import PiperLocalTTSAdapter
from runtime.voice.models import VoiceProviderSpec

# hermes-audio routes STT/TTS through the Hermes runtime; it is optional and absent in
# the MIT Core build (which has no runtime.hermes). Guarded so voice runs on the
# open-source local defaults (whisper/piper) without it.
try:
    from runtime.voice.adapters.hermes_audio import HermesAudioSTTAdapter, HermesAudioTTSAdapter

    _HERMES_AUDIO_AVAILABLE = True
except Exception:  # noqa: BLE001 - runtime.hermes not installed (e.g. MIT Core build)
    _HERMES_AUDIO_AVAILABLE = False

# Open-source, on-device defaults (no API key, privacy-first).
_DEFAULT_STT_PROVIDER = "local-whisper"
_DEFAULT_TTS_PROVIDER = "piper-local"

# Declared providers. ``implemented=True`` marks the ones with a live adapter wired
# below. Declaration is not capability — an adapter being live still depends on the
# engine being installed/configured (its ``readiness()`` reports that honestly).
_VOICE_PROVIDERS: tuple[VoiceProviderSpec, ...] = (
    VoiceProviderSpec("local-whisper", "stt", "local", "", True,
                      "On-device faster-whisper; open-source, no network, no provider key. (default STT)"),
    VoiceProviderSpec("hermes-audio", "stt", "bus", "", True,
                      "Route STT through the Hermes runtime; capability-gated (live when the gateway exposes audio_api)."),
    VoiceProviderSpec("openai-whisper", "stt", "direct", "OPENAI_API_KEY", False,
                      "OpenAI transcription endpoint; backend-owned credential."),
    VoiceProviderSpec("piper-local", "tts", "local", "", True,
                      "On-device Piper neural TTS; open-source, no network, no provider key. (default TTS)"),
    VoiceProviderSpec("hermes-audio", "tts", "bus", "", True,
                      "Route TTS through the Hermes runtime; capability-gated (live when the gateway exposes audio_api)."),
    VoiceProviderSpec("openai-tts", "tts", "direct", "OPENAI_API_KEY", False,
                      "OpenAI speech endpoint; backend-owned credential."),
    VoiceProviderSpec("elevenlabs", "tts", "direct", "ELEVENLABS_API_KEY", False,
                      "ElevenLabs voices; backend-owned credential."),
)

# Live adapter classes by provider id. Open-source local engines are wired by default;
# hermes-audio is wired but capability-gated (blocked until the gateway exposes audio_api).
_STT_ADAPTERS: dict[str, type[STTAdapter]] = {
    "local-whisper": LocalWhisperSTTAdapter,
}
_TTS_ADAPTERS: dict[str, type[TTSAdapter]] = {
    "piper-local": PiperLocalTTSAdapter,
}
if _HERMES_AUDIO_AVAILABLE:
    _STT_ADAPTERS["hermes-audio"] = HermesAudioSTTAdapter
    _TTS_ADAPTERS["hermes-audio"] = HermesAudioTTSAdapter


def voice_lane_enabled() -> bool:
    # CHASEOS_VOICE_DISABLED is a hard kill; otherwise the persisted master setting decides.
    if str(os.environ.get("CHASEOS_VOICE_DISABLED") or "").strip().lower() in {"1", "true", "yes", "on"}:
        return False
    try:
        from runtime.voice.settings import load_voice_settings

        return bool(load_voice_settings().get("enabled", True))
    except Exception:  # noqa: BLE001 - fail-open to enabled
        return True


def list_voice_providers(kind: str = "") -> list[dict]:
    """Declared providers, optionally filtered by ``kind`` ('stt' | 'tts')."""
    want = str(kind or "").strip().lower()
    return [p.to_dict() for p in _VOICE_PROVIDERS if not want or p.kind == want]


def _configured(kind: str) -> str:
    env = "CHASEOS_VOICE_STT_PROVIDER" if kind == "stt" else "CHASEOS_VOICE_TTS_PROVIDER"
    default = _DEFAULT_STT_PROVIDER if kind == "stt" else _DEFAULT_TTS_PROVIDER
    return str(os.environ.get(env) or "").strip() or default


def resolve_stt_adapter() -> STTAdapter:
    """Return the active STT adapter. Defaults to the open-source local-whisper
    engine; falls back to the honest null adapter when the lane is disabled or no
    live adapter is registered for the configured id."""
    if not voice_lane_enabled():
        return NullSTTAdapter(intended_provider="(voice lane disabled)")
    provider = _configured("stt")
    cls = _STT_ADAPTERS.get(provider)
    if cls is not None:
        return cls()
    return NullSTTAdapter(intended_provider=provider)


def resolve_tts_adapter() -> TTSAdapter:
    """Return the active TTS adapter. Defaults to the open-source piper-local
    engine; falls back to the honest null adapter when the lane is disabled or no
    live adapter is registered for the configured id."""
    if not voice_lane_enabled():
        return NullTTSAdapter(intended_provider="(voice lane disabled)")
    provider = _configured("tts")
    cls = _TTS_ADAPTERS.get(provider)
    if cls is not None:
        return cls()
    return NullTTSAdapter(intended_provider=provider)


def register_stt_adapter(provider_id: str, adapter_cls: type[STTAdapter]) -> None:
    """Register a live STT adapter (used by future backend wiring + tests)."""
    _STT_ADAPTERS[str(provider_id)] = adapter_cls


def register_tts_adapter(provider_id: str, adapter_cls: type[TTSAdapter]) -> None:
    """Register a live TTS adapter (used by future backend wiring + tests)."""
    _TTS_ADAPTERS[str(provider_id)] = adapter_cls
