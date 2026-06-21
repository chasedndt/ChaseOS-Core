"""Honest no-op voice adapters — the default until a live backend is mounted.

These perform NO microphone capture, NO audio file writes, NO provider calls,
and NO synthesis. They exist so the whole voice path is wired end-to-end and the
UI can show the exact blocked reason, without ever pretending audio works.
"""

from __future__ import annotations

from runtime.voice.adapters.base import STTAdapter, TTSAdapter
from runtime.voice.models import STTRequest, STTResult, TTSRequest, TTSResult

_STT_BLOCKED = (
    "Voice transcription is not live yet. No STT backend is mounted "
    "(the Hermes gateway reports audio_api: false). Configure a voice STT "
    "provider and adapter before transcription can run."
)
_TTS_BLOCKED = (
    "Voice synthesis is not live yet. No TTS backend is mounted "
    "(the Hermes gateway reports audio_api: false). Configure a voice TTS "
    "provider and adapter before synthesis can run."
)


class NullSTTAdapter(STTAdapter):
    provider_id = "null"
    transport = "none"

    def __init__(self, *, intended_provider: str = "") -> None:
        self.intended_provider = intended_provider

    def transcribe(self, request: STTRequest) -> STTResult:  # noqa: ARG002 - no-op by design
        return STTResult(
            ok=False,
            transcript="",
            provider_id=self.provider_id,
            bridge="voice_null_stt",
            blocked_reason=_STT_BLOCKED,
        )

    def readiness(self) -> dict:
        return {
            "live": False,
            "provider_id": self.provider_id,
            "intended_provider": self.intended_provider,
            "blocked_reason": _STT_BLOCKED,
        }


class NullTTSAdapter(TTSAdapter):
    provider_id = "null"
    transport = "none"

    def __init__(self, *, intended_provider: str = "") -> None:
        self.intended_provider = intended_provider

    def synthesize(self, request: TTSRequest) -> TTSResult:  # noqa: ARG002 - no-op by design
        return TTSResult(
            ok=False,
            audio_path=None,
            audio_bytes=0,
            provider_id=self.provider_id,
            bridge="voice_null_tts",
            blocked_reason=_TTS_BLOCKED,
        )

    def readiness(self) -> dict:
        return {
            "live": False,
            "provider_id": self.provider_id,
            "intended_provider": self.intended_provider,
            "blocked_reason": _TTS_BLOCKED,
        }
