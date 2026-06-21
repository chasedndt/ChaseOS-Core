"""Voice subsystem data models + authority flags.

DOM-free, provider-free, dependency-free dataclasses. These define the seam the
STT/TTS adapters speak across. The authority block makes the no-effect guarantee
explicit and machine-checkable: every flag defaults False, and the foundation
never flips one to True.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class VoiceAuthority:
    """Honest record of what a voice operation actually did.

    The foundation keeps every flag False. A future live adapter may only set a
    flag True after the corresponding gated capability genuinely executes.
    """

    microphone_capture_performed: bool = False
    audio_file_written: bool = False
    stt_provider_called: bool = False
    tts_provider_called: bool = False
    audio_played: bool = False
    canonical_writeback: bool = False

    def to_dict(self) -> dict[str, bool]:
        return asdict(self)


@dataclass
class STTRequest:
    """A speech-to-text request. ``audio_ref`` is a vault-scoped path or handle;
    the foundation never reads or captures audio."""

    audio_ref: str = ""
    language: str = ""
    session_id: str = ""
    provider_id: str = ""


@dataclass
class STTResult:
    ok: bool = False
    transcript: str = ""
    provider_id: str = ""
    bridge: str = ""
    blocked_reason: str | None = None
    authority: VoiceAuthority = field(default_factory=VoiceAuthority)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["authority"] = self.authority.to_dict()
        return data


@dataclass
class TTSRequest:
    """A text-to-speech request. ``text`` is what would be synthesized; the
    foundation never synthesizes or plays audio."""

    text: str = ""
    voice: str = ""
    session_id: str = ""
    provider_id: str = ""
    output_path: str = ""   # where to write the synthesized audio; "" → engine temp file


@dataclass
class TTSResult:
    ok: bool = False
    audio_path: str | None = None
    audio_bytes: int = 0
    provider_id: str = ""
    bridge: str = ""
    blocked_reason: str | None = None
    authority: VoiceAuthority = field(default_factory=VoiceAuthority)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["authority"] = self.authority.to_dict()
        return data


@dataclass
class AudioCaptureResult:
    ok: bool = False
    audio_path: str | None = None
    seconds: float = 0.0
    samplerate: int = 0
    backend: str = ""
    session_id: str = ""          # for start/stop capture sessions
    reason: str = ""              # how capture ended: stopped | silence | start_timeout | max | error
    speech_detected: bool = False  # VAD: was any speech heard before it ended
    blocked_reason: str | None = None
    authority: VoiceAuthority = field(default_factory=VoiceAuthority)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["authority"] = self.authority.to_dict()
        return data


@dataclass
class AudioPlaybackResult:
    ok: bool = False
    audio_path: str | None = None
    backend: str = ""
    blocked_reason: str | None = None
    authority: VoiceAuthority = field(default_factory=VoiceAuthority)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["authority"] = self.authority.to_dict()
        return data


@dataclass
class VoiceProviderSpec:
    """A declared (non-exhaustive) voice provider. Declaration is not capability:
    a spec being present does NOT mean an adapter is implemented or live."""

    provider_id: str
    kind: str               # "stt" | "tts"
    transport: str          # "bus" | "direct" | "local"
    env_var: str = ""       # credential env var owned by the backend, never read by Studio
    implemented: bool = False
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VoiceReadiness:
    """Honest, UI-facing readiness verdict for the voice lane (analogue of
    ``hermes_delivery_truth`` for chat)."""

    surface: str = "voice_delivery_truth"
    transport_enabled: bool = False
    stt_provider: str = ""
    tts_provider: str = ""
    stt_adapter_live: bool = False
    tts_adapter_live: bool = False
    can_transcribe: bool = False
    can_synthesize: bool = False
    blocked_reason: str | None = None
    authority: VoiceAuthority = field(default_factory=VoiceAuthority)
    providers: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["authority"] = self.authority.to_dict()
        return data
