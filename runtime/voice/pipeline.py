"""End-to-end voice pipelines: mic→text and text→speech.

Ties the OS audio I/O layer to the open-source STT/TTS adapters. Every step is
honest-gated: if any stage has no live backend (no mic, engine not installed, no
playback), the pipeline stops with that stage's bounded blocked reason and merges
the authority of whatever genuinely ran.
"""

from __future__ import annotations

from runtime.voice.audio_io import capture_microphone, play_audio
from runtime.voice.models import STTRequest, TTSRequest, VoiceAuthority
from runtime.voice.provider_registry import resolve_stt_adapter, resolve_tts_adapter


def _merge_authority(*auths: VoiceAuthority) -> dict:
    merged = VoiceAuthority()
    for a in auths:
        if a is None:
            continue
        for key, value in a.to_dict().items():
            if value:
                setattr(merged, key, True)
    return merged.to_dict()


def transcribe_microphone(*, seconds: float = 5.0, consent: bool = False, language: str = "") -> dict:
    """Record from the mic (with consent) and transcribe it via the STT adapter.

    Returns ``{ok, transcript, stage, blocked_reason, audio_path, authority}``.
    """
    capture = capture_microphone(seconds=seconds, consent=consent)
    if not capture.ok:
        return {
            "ok": False,
            "stage": "capture",
            "transcript": "",
            "audio_path": capture.audio_path,
            "blocked_reason": capture.blocked_reason,
            "authority": _merge_authority(capture.authority),
        }
    stt = resolve_stt_adapter()
    result = stt.transcribe(STTRequest(audio_ref=capture.audio_path or "", language=language))
    return {
        "ok": bool(result.ok),
        "stage": "transcribe" if result.ok else "transcribe_blocked",
        "transcript": result.transcript,
        "audio_path": capture.audio_path,
        "provider_id": result.provider_id,
        "blocked_reason": result.blocked_reason,
        "authority": _merge_authority(capture.authority, result.authority),
    }


def speak_text(text: str, *, voice: str = "", output_path: str = "", play: bool = True) -> dict:
    """Synthesize ``text`` via the TTS adapter and (optionally) play it.

    Returns ``{ok, stage, audio_path, played, blocked_reason, authority}``.
    """
    tts = resolve_tts_adapter()
    synth = tts.synthesize(TTSRequest(text=text, voice=voice, output_path=output_path))
    if not synth.ok:
        return {
            "ok": False,
            "stage": "synthesize",
            "audio_path": None,
            "played": False,
            "provider_id": synth.provider_id,
            "blocked_reason": synth.blocked_reason,
            "authority": _merge_authority(synth.authority),
        }
    if not play:
        return {
            "ok": True,
            "stage": "synthesize",
            "audio_path": synth.audio_path,
            "played": False,
            "provider_id": synth.provider_id,
            "blocked_reason": None,
            "authority": _merge_authority(synth.authority),
        }
    playback = play_audio(synth.audio_path or "")
    return {
        "ok": bool(playback.ok),
        "stage": "play" if playback.ok else "play_blocked",
        "audio_path": synth.audio_path,
        "played": bool(playback.ok),
        "provider_id": synth.provider_id,
        "blocked_reason": playback.blocked_reason,
        "authority": _merge_authority(synth.authority, playback.authority),
    }
