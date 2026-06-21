"""STT/TTS adapter contracts.

A voice adapter is the only thing allowed to touch an audio provider, and only
the backend that owns the adapter holds the credentials. Studio resolves an
adapter through the registry and calls these methods — it never constructs a
provider client itself. This mirrors the LLM provider-agnostic rule for audio.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator

from runtime.voice.models import STTRequest, STTResult, TTSRequest, TTSResult


class STTAdapter(ABC):
    """Speech-to-text adapter. ``provider_id`` identifies the backend."""

    provider_id: str = "base"
    transport: str = "bus"
    supports_streaming: bool = False

    @abstractmethod
    def transcribe(self, request: STTRequest) -> STTResult:
        """Transcribe audio referenced by ``request`` into text.

        Implementations MUST be bounded and MUST set ``authority`` honestly. A
        non-live adapter returns ``ok=False`` with a ``blocked_reason`` and no
        authority flags set.
        """

    def transcribe_stream(self, request: STTRequest) -> Iterator[dict]:
        """Yield incremental transcription chunks.

        Each chunk: ``{"partial": str, "transcript": str, "done": bool, "ok": bool,
        "blocked_reason": str|None}`` where ``transcript`` is the cumulative text so
        far. Default implementation wraps ``transcribe()`` as a single final chunk —
        adapters with a true streaming engine override this. Never fabricates partials.
        """
        result = self.transcribe(request)
        yield {
            "partial": result.transcript if result.ok else "",
            "transcript": result.transcript,
            "done": True,
            "ok": bool(result.ok),
            "blocked_reason": result.blocked_reason,
        }

    @abstractmethod
    def readiness(self) -> dict:
        """Return a bounded readiness dict: ``{live, provider_id, blocked_reason}``."""

    @property
    def live(self) -> bool:
        return bool(self.readiness().get("live"))


class TTSAdapter(ABC):
    """Text-to-speech adapter. ``provider_id`` identifies the backend."""

    provider_id: str = "base"
    transport: str = "bus"

    @abstractmethod
    def synthesize(self, request: TTSRequest) -> TTSResult:
        """Synthesize ``request.text`` into audio.

        Implementations MUST be bounded and MUST set ``authority`` honestly. A
        non-live adapter returns ``ok=False`` with a ``blocked_reason`` and no
        authority flags set.
        """

    @abstractmethod
    def readiness(self) -> dict:
        """Return a bounded readiness dict: ``{live, provider_id, blocked_reason}``."""

    @property
    def live(self) -> bool:
        return bool(self.readiness().get("live"))
