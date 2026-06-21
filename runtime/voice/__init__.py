"""ChaseOS Voice subsystem — provider-agnostic STT/TTS foundation (Item E).

This package is the modular foundation for Studio voice (speech-to-text +
text-to-speech). It follows the canonical Provider-Agnostic Routing rule
(``06_AGENTS/Provider-Agnostic-Routing-Architecture.md``): Studio/UI surfaces
never call audio providers directly. Voice routes through a provider-agnostic
adapter layer; each backend owns its own credentials and model choice.

Current reality (2026-06-18): NO live audio backend is mounted. The live Hermes
gateway reports ``audio_api: false`` / ``realtime_voice: false``. The default
adapters are honest no-ops that perform no microphone capture, no audio file
writes, no provider calls, and no synthesis — they report a bounded blocked
state so the UI can show the exact reason before any audio action.

Public surface:
- ``models`` — dataclasses + authority flags
- ``adapters`` — ``STTAdapter`` / ``TTSAdapter`` ABCs + null adapters
- ``provider_registry`` — declared (non-exhaustive) providers + resolution
- ``readiness`` — ``voice_delivery_truth()`` honest readiness report
"""

from runtime.voice.models import (
    VoiceAuthority,
    STTRequest,
    STTResult,
    TTSRequest,
    TTSResult,
    VoiceProviderSpec,
    VoiceReadiness,
)
from runtime.voice.readiness import voice_delivery_truth
from runtime.voice.provider_registry import (
    list_voice_providers,
    resolve_stt_adapter,
    resolve_tts_adapter,
)
from runtime.voice.audio_io import (
    audio_io_readiness,
    capture_microphone,
    capture_until_silence,
    start_capture,
    stop_capture,
    vad_should_stop,
    microphone_available,
    play_audio,
    playback_available,
)
from runtime.voice.pipeline import speak_text, transcribe_microphone

__all__ = [
    "VoiceAuthority",
    "STTRequest",
    "STTResult",
    "TTSRequest",
    "TTSResult",
    "VoiceProviderSpec",
    "VoiceReadiness",
    "voice_delivery_truth",
    "list_voice_providers",
    "resolve_stt_adapter",
    "resolve_tts_adapter",
    "audio_io_readiness",
    "capture_microphone",
    "capture_until_silence",
    "start_capture",
    "stop_capture",
    "vad_should_stop",
    "play_audio",
    "microphone_available",
    "playback_available",
    "transcribe_microphone",
    "speak_text",
]
