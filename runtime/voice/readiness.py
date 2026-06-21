"""Honest voice-lane readiness — the analogue of ``hermes_delivery_truth`` for chat.

Lets the UI show the EXACT blocker before any voice action. Read-only and
config-first: it inspects the resolved adapters + declared providers and never
captures audio, calls a provider, or writes a file. ``probe=True`` may
optionally check whether the Hermes gateway advertises an audio API (it does not
today), but defaults to no network.
"""

from __future__ import annotations

from runtime.voice.models import VoiceAuthority, VoiceReadiness
from runtime.voice.provider_registry import (
    list_voice_providers,
    resolve_stt_adapter,
    resolve_tts_adapter,
    voice_lane_enabled,
)


def _hermes_audio_supported(*, timeout_seconds: int = 4) -> bool | None:
    """Best-effort: does the Hermes gateway advertise an audio API? None if unknown.

    Reuses the chat bridge's capability probe (which reads the gateway's nested
    ``features`` map). Fail-open: any error → None (unknown), never an exception.
    """
    try:
        from runtime.hermes.chat_bridge import hermes_api_capabilities

        caps = hermes_api_capabilities(timeout_seconds=timeout_seconds)
        if not caps.get("available"):
            return None
        features = caps.get("features") if isinstance(caps.get("features"), dict) else {}
        return bool(features.get("audio_api") or features.get("realtime_voice"))
    except Exception:
        return None


def voice_delivery_truth(*, probe: bool = False, timeout_seconds: int = 4) -> dict:
    """Return a bounded, honest readiness verdict for the voice lane."""
    enabled = voice_lane_enabled()
    stt = resolve_stt_adapter()
    tts = resolve_tts_adapter()
    stt_ready = stt.readiness()
    tts_ready = tts.readiness()

    readiness = VoiceReadiness(
        transport_enabled=enabled,
        stt_provider=stt.provider_id,
        tts_provider=tts.provider_id,
        stt_adapter_live=bool(stt_ready.get("live")),
        tts_adapter_live=bool(tts_ready.get("live")),
        can_transcribe=bool(stt_ready.get("live")),
        can_synthesize=bool(tts_ready.get("live")),
        authority=VoiceAuthority(),  # foundation performs nothing
        providers=list_voice_providers(),
    )

    if not enabled:
        readiness.blocked_reason = "Voice lane disabled (CHASEOS_VOICE_DISABLED)."
        return readiness.to_dict()

    if not (readiness.stt_adapter_live or readiness.tts_adapter_live):
        # Surface the adapters' own specific reasons (e.g. "faster-whisper not installed",
        # "no Piper voice model configured") instead of a generic message.
        parts = []
        stt_reason = stt_ready.get("blocked_reason")
        tts_reason = tts_ready.get("blocked_reason")
        if stt_reason:
            parts.append(f"STT ({stt.provider_id}): {stt_reason}")
        if tts_reason:
            parts.append(f"TTS ({tts.provider_id}): {tts_reason}")
        if probe:
            supported = _hermes_audio_supported(timeout_seconds=timeout_seconds)
            if supported is False:
                parts.append("Hermes gateway reports no audio API (audio_api: false).")
            elif supported is None:
                parts.append("Hermes gateway audio capability is unknown (not probed/unreachable).")
        readiness.blocked_reason = "  ".join(parts) or (
            "Voice is not live yet: no STT/TTS adapter is mounted."
        )
    out = readiness.to_dict()
    # Audio I/O layer (mic capture + playback) + end-to-end composite capability.
    try:
        from runtime.voice.audio_io import audio_io_readiness

        audio = audio_io_readiness()
    except Exception:  # noqa: BLE001 - fail-open
        audio = {"can_capture": False, "can_play": False, "capture_backend": "", "playback_backend": "", "blocked_reason": "audio_io_unavailable"}
    out["audio_io"] = audio
    out["can_transcribe_microphone"] = bool(out.get("can_transcribe") and audio.get("can_capture"))
    out["can_speak"] = bool(out.get("can_synthesize") and audio.get("can_play"))
    return out
