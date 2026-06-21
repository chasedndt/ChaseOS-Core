"""local-whisper STT adapter — open-source, on-device, no API key.

Uses faster-whisper (CTranslate2 Whisper) when it is installed. Fully local: no
network, no provider credential, privacy-first. When the engine is not installed
the adapter is honest-blocked (it never pretends to transcribe).

Offline model: a CTranslate2 Whisper model staged in ``~/.chaseos/voice/whisper/<size>/``
(or bundled into a packaged build under ``sys._MEIPASS/voice/whisper/<size>/``) is loaded
directly — fully air-gapped, no download. Otherwise the model size triggers a one-time
online download on first use.

Env:
- ``CHASEOS_VOICE_WHISPER_MODEL`` — model size/id, or a path to a local model dir (default ``base``)
- ``CHASEOS_VOICE_WHISPER_DEVICE`` — ``cpu`` (default) | ``cuda``
- ``CHASEOS_VOICE_WHISPER_COMPUTE`` — compute type (default ``int8``)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Iterator

from runtime.voice.adapters.base import STTAdapter
from runtime.voice.models import STTRequest, STTResult, VoiceAuthority

_PROVIDER = "local-whisper"
_MAX_TRANSCRIPT_CHARS = 20000


def _faster_whisper_available() -> bool:
    try:
        import importlib.util

        return importlib.util.find_spec("faster_whisper") is not None
    except Exception:
        return False


def _model_name() -> str:
    # Top override: CHASEOS_VOICE_WHISPER_MODEL (a size OR a model dir path).
    explicit = str(os.environ.get("CHASEOS_VOICE_WHISPER_MODEL") or "").strip()
    if explicit:
        return explicit
    # Else the persisted setting (which itself honors CHASEOS_VOICE_STT_SIZE) → default "base".
    try:
        from runtime.voice.settings import effective_stt_size

        return effective_stt_size() or "base"
    except Exception:  # noqa: BLE001 - fail-open
        return "base"


def _whisper_dirs() -> list[Path]:
    """Candidate dirs holding a staged/bundled CTranslate2 Whisper model, priority order:
    1. PyInstaller bundle (sys._MEIPASS/voice/whisper) — air-gapped packaged build.
    2. The conventional per-user dir (~/.chaseos/voice/whisper).
    """
    dirs: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        dirs.append(Path(meipass) / "voice" / "whisper")
    dirs.append(Path.home() / ".chaseos" / "voice" / "whisper")
    return dirs


def _resolve_model() -> str:
    """Resolve what to hand WhisperModel().

    Returns a LOCAL model directory (fully offline) when a staged/bundled CTranslate2 model
    is present, otherwise the model size/id (which triggers a one-time online download on
    first use). An explicit ``CHASEOS_VOICE_WHISPER_MODEL`` that is itself a model dir wins.
    """
    name = _model_name()
    explicit = Path(name)
    if explicit.is_dir() and (explicit / "model.bin").is_file():
        return str(explicit)
    for base in _whisper_dirs():
        sized = base / name
        if (sized / "model.bin").is_file():
            return str(sized)
        if (base / "model.bin").is_file():
            return str(base)
    return name  # online fallback (size/id)


def _model_is_offline() -> bool:
    """Whether STT will load from a staged/bundled local model (no network)."""
    resolved = _resolve_model()
    return Path(resolved).is_dir() and (Path(resolved) / "model.bin").is_file()


class LocalWhisperSTTAdapter(STTAdapter):
    provider_id = _PROVIDER
    transport = "local"
    supports_streaming = True

    # Process-level model cache so repeated transcriptions don't reload weights.
    _model = None

    def readiness(self) -> dict:
        if not _faster_whisper_available():
            return {
                "live": False,
                "provider_id": self.provider_id,
                "blocked_reason": (
                    "faster-whisper is not installed. Install the open-source engine "
                    "(`pip install faster-whisper`) to enable on-device transcription. "
                    "No API key is required."
                ),
            }
        return {
            "live": True,
            "provider_id": self.provider_id,
            "blocked_reason": None,
            "model": _model_name(),
            "model_source": "bundled-offline" if _model_is_offline() else "download-on-first-use",
        }

    def _get_model(self):
        if LocalWhisperSTTAdapter._model is None:
            from faster_whisper import WhisperModel

            # _resolve_model() returns a local dir (offline) when staged/bundled, else the size.
            LocalWhisperSTTAdapter._model = WhisperModel(
                _resolve_model(),
                device=str(os.environ.get("CHASEOS_VOICE_WHISPER_DEVICE") or "cpu"),
                compute_type=str(os.environ.get("CHASEOS_VOICE_WHISPER_COMPUTE") or "int8"),
            )
        return LocalWhisperSTTAdapter._model

    def transcribe(self, request: STTRequest) -> STTResult:
        ready = self.readiness()
        if not ready.get("live"):
            return STTResult(ok=False, provider_id=self.provider_id, bridge="voice_local_whisper",
                             blocked_reason=ready.get("blocked_reason"))
        audio_ref = str(request.audio_ref or "").strip()
        if not audio_ref:
            return STTResult(ok=False, provider_id=self.provider_id, bridge="voice_local_whisper",
                             blocked_reason="No audio_ref supplied for transcription.")
        audio_path = Path(audio_ref)
        if not audio_path.is_file():
            return STTResult(ok=False, provider_id=self.provider_id, bridge="voice_local_whisper",
                             blocked_reason=f"Audio file not found: {audio_ref}")
        try:
            model = self._get_model()
            kwargs = {}
            if str(request.language or "").strip():
                kwargs["language"] = request.language.strip()
            segments, _info = model.transcribe(str(audio_path), **kwargs)
            text = "".join(getattr(seg, "text", "") for seg in segments).strip()
        except Exception as exc:  # noqa: BLE001 - bounded
            return STTResult(ok=False, provider_id=self.provider_id, bridge="voice_local_whisper",
                             blocked_reason=f"Transcription failed: {type(exc).__name__}.")
        if not text:
            return STTResult(ok=False, provider_id=self.provider_id, bridge="voice_local_whisper",
                             blocked_reason="Transcription produced no text.")
        if len(text) > _MAX_TRANSCRIPT_CHARS:
            text = text[:_MAX_TRANSCRIPT_CHARS].rstrip() + "…"
        # The local engine genuinely ran; record it honestly. No file written, no network.
        return STTResult(
            ok=True,
            transcript=text,
            provider_id=self.provider_id,
            bridge="voice_local_whisper",
            authority=VoiceAuthority(stt_provider_called=True),
        )

    def transcribe_stream(self, request: STTRequest) -> Iterator[dict]:
        """Stream cumulative partials as faster-whisper decodes each segment.

        faster-whisper's ``transcribe()`` returns a lazy segment generator — iterating
        it yields segments as they are produced, so we can emit growing partials and a
        final ``done`` chunk. Honest-blocked (single chunk) when the engine/audio is absent.
        """
        ready = self.readiness()
        if not ready.get("live"):
            yield {"partial": "", "transcript": "", "done": True, "ok": False,
                   "blocked_reason": ready.get("blocked_reason")}
            return
        audio_ref = str(request.audio_ref or "").strip()
        if not audio_ref or not Path(audio_ref).is_file():
            yield {"partial": "", "transcript": "", "done": True, "ok": False,
                   "blocked_reason": f"Audio file not found: {audio_ref}"}
            return
        try:
            model = self._get_model()
            kwargs = {}
            if str(request.language or "").strip():
                kwargs["language"] = request.language.strip()
            segments, _info = model.transcribe(str(audio_ref), **kwargs)
            cumulative = ""
            for seg in segments:
                piece = getattr(seg, "text", "") or ""
                cumulative += piece
                trimmed = cumulative.strip()
                if len(trimmed) > _MAX_TRANSCRIPT_CHARS:
                    trimmed = trimmed[:_MAX_TRANSCRIPT_CHARS].rstrip() + "…"
                yield {"partial": piece, "transcript": trimmed, "done": False, "ok": True, "blocked_reason": None}
        except Exception as exc:  # noqa: BLE001 - bounded
            yield {"partial": "", "transcript": "", "done": True, "ok": False,
                   "blocked_reason": f"Transcription failed: {type(exc).__name__}."}
            return
        final = cumulative.strip()
        if not final:
            yield {"partial": "", "transcript": "", "done": True, "ok": False,
                   "blocked_reason": "Transcription produced no text."}
            return
        if len(final) > _MAX_TRANSCRIPT_CHARS:
            final = final[:_MAX_TRANSCRIPT_CHARS].rstrip() + "…"
        yield {"partial": "", "transcript": final, "done": True, "ok": True, "blocked_reason": None}
