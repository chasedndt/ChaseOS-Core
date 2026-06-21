"""OS audio I/O layer for the voice lane — microphone capture + playback.

Open-source / local only: no network, no provider key. Multi-backend with
honest availability detection — real work when a backend is present, a bounded
blocked state otherwise (consistent with the engine adapters).

Privacy gate: ``capture_microphone`` records the operator. It will NOT record
unless called with ``consent=True``. This is an explicit, auditable opt-in; the
authority flags record exactly what happened.

Backends
- Capture: ``sounddevice`` (PortAudio) → WAV via stdlib ``wave``. (``pyaudio`` is a
  future option behind the same seam.)
- Playback: ``winsound`` (Windows, stdlib) / ``afplay`` (macOS) / ``aplay`` |
  ``paplay`` (Linux) — all dependency-free; ``sounddevice`` is a future option.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import wave
from pathlib import Path
from typing import Any

from runtime.voice.models import AudioCaptureResult, AudioPlaybackResult, VoiceAuthority

_DEFAULT_SAMPLERATE = 16000
_MAX_CAPTURE_SECONDS = 120
# Generous safety ceiling for open-ended capture (voice notes run until the user stops or
# until trailing silence; this only guards against a truly runaway/forgotten recording).
# Set CHASEOS_VOICE_MAX_CAPTURE_SECONDS=0 for no ceiling.
_DEFAULT_MAX_CAPTURE_SECONDS = 600.0
# Default voice-activity-detection tuning for continuous mode.
_DEFAULT_SILENCE_HOLD_SECONDS = 1.2     # trailing silence that ends an utterance
_DEFAULT_START_TIMEOUT_SECONDS = 8.0    # give up if no speech ever starts
_DEFAULT_ENERGY_THRESHOLD = 0.012       # normalized RMS above which a block counts as speech


def _max_capture_ceiling(explicit: float | None = None) -> float:
    """Resolve the open-ended capture safety ceiling (0 / negative = unlimited)."""
    if explicit is not None:
        return float(explicit)
    raw = str(os.environ.get("CHASEOS_VOICE_MAX_CAPTURE_SECONDS") or "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return _DEFAULT_MAX_CAPTURE_SECONDS


# ── detection ────────────────────────────────────────────────────────────────

def _sounddevice_available() -> bool:
    try:
        import importlib.util

        return (
            importlib.util.find_spec("sounddevice") is not None
            and importlib.util.find_spec("numpy") is not None
        )
    except Exception:
        return False


def capture_backend() -> str:
    """Return the active capture backend id, or '' if none is available."""
    if _sounddevice_available():
        return "sounddevice"
    return ""


def playback_backend() -> str:
    """Return the active playback backend id, or '' if none is available."""
    if os.name == "nt":
        return "winsound"  # stdlib on Windows
    if sys.platform == "darwin" and shutil.which("afplay"):
        return "afplay"
    for tool in ("paplay", "aplay"):
        if shutil.which(tool):
            return tool
    return ""


def microphone_available() -> bool:
    return bool(capture_backend())


def playback_available() -> bool:
    return bool(playback_backend())


def audio_io_readiness() -> dict:
    """Honest, read-only audio I/O readiness (capture + playback)."""
    cap = capture_backend()
    play = playback_backend()
    reasons = []
    if not cap:
        reasons.append(
            "Microphone capture has no backend. Install the open-source `sounddevice` "
            "(PortAudio) package — `pip install sounddevice numpy` — to enable recording."
        )
    if not play:
        reasons.append(
            "Audio playback has no backend (winsound/afplay/aplay/paplay not found)."
        )
    return {
        "capture_backend": cap,
        "playback_backend": play,
        "can_capture": bool(cap),
        "can_play": bool(play),
        "consent_required_for_capture": True,
        "blocked_reason": "  ".join(reasons) or None,
    }


# ── capture ──────────────────────────────────────────────────────────────────

def capture_microphone(
    *,
    seconds: float = 5.0,
    output_path: str = "",
    samplerate: int = _DEFAULT_SAMPLERATE,
    consent: bool = False,
) -> AudioCaptureResult:
    """Record ``seconds`` of mono audio from the default mic to a WAV file.

    Records ONLY when ``consent=True``. Bounded duration. Returns the WAV path so
    an STT adapter can transcribe it. Honest-blocked when no backend or no consent.
    """
    if not consent:
        return AudioCaptureResult(
            ok=False,
            blocked_reason="Microphone capture requires explicit consent (consent=True). Not recorded.",
        )
    backend = capture_backend()
    if not backend:
        return AudioCaptureResult(ok=False, backend="", blocked_reason=audio_io_readiness()["blocked_reason"])
    try:
        secs = max(0.1, min(float(seconds), _MAX_CAPTURE_SECONDS))
    except (TypeError, ValueError):
        secs = 5.0
    out = str(output_path or "").strip()
    if not out:
        import tempfile

        fd, out = tempfile.mkstemp(prefix="chaseos_mic_", suffix=".wav")
        os.close(fd)
    try:
        import numpy as np  # noqa: F401 - required by sounddevice array path
        import sounddevice as sd

        frames = int(secs * samplerate)
        recording = sd.rec(frames, samplerate=samplerate, channels=1, dtype="int16")
        sd.wait()
        with wave.open(out, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)  # int16
            wav.setframerate(samplerate)
            wav.writeframes(recording.tobytes())
    except Exception as exc:  # noqa: BLE001 - bounded
        return AudioCaptureResult(ok=False, backend=backend,
                                  blocked_reason=f"Microphone capture failed: {type(exc).__name__}.")
    produced = Path(out)
    if not produced.is_file() or produced.stat().st_size == 0:
        return AudioCaptureResult(ok=False, backend=backend, blocked_reason="Capture produced no audio.")
    # Genuine recording happened and a file was written — record it honestly.
    return AudioCaptureResult(
        ok=True,
        audio_path=str(produced),
        seconds=secs,
        samplerate=samplerate,
        backend=backend,
        reason="fixed",
        authority=VoiceAuthority(microphone_capture_performed=True, audio_file_written=True),
    )


# ── voice-activity detection (VAD) — pure, unit-testable core ────────────────

def block_rms(int16_array: Any) -> float:
    """Normalized RMS energy (0..~1) of an int16 PCM block. numpy in, scalar out."""
    import numpy as np

    if int16_array is None or len(int16_array) == 0:
        return 0.0
    samples = np.asarray(int16_array, dtype=np.float32) / 32768.0
    return float(np.sqrt(np.mean(np.square(samples))))


def vad_should_stop(
    state: dict,
    *,
    rms: float,
    now: float,
    silence_hold_seconds: float = _DEFAULT_SILENCE_HOLD_SECONDS,
    start_timeout_seconds: float = _DEFAULT_START_TIMEOUT_SECONDS,
    max_seconds: float = _DEFAULT_MAX_CAPTURE_SECONDS,
    energy_threshold: float = _DEFAULT_ENERGY_THRESHOLD,
) -> str | None:
    """Pure VAD step. Mutates ``state`` ({started_at, speech_started, last_voice_at}) and
    returns a stop reason ('silence' | 'start_timeout' | 'max') or None to keep recording.

    Continuous mode: wait for speech to start; once it has, stop after a trailing pause of
    ``silence_hold_seconds``. No fixed cap on utterance length — only the silence pause ends
    it (and ``max_seconds`` is a safety ceiling; <= 0 disables it).
    """
    if "started_at" not in state:
        state["started_at"] = now
        state["speech_started"] = False
        state["last_voice_at"] = now
    if rms >= energy_threshold:
        state["speech_started"] = True
        state["last_voice_at"] = now
    if max_seconds and max_seconds > 0 and (now - state["started_at"]) >= max_seconds:
        return "max"
    if not state["speech_started"]:
        if (now - state["started_at"]) >= start_timeout_seconds:
            return "start_timeout"
        return None
    if (now - state["last_voice_at"]) >= silence_hold_seconds:
        return "silence"
    return None


def _open_wav(path: str, samplerate: int) -> Any:
    wav = wave.open(path, "wb")
    wav.setnchannels(1)
    wav.setsampwidth(2)  # int16
    wav.setframerate(samplerate)
    return wav


def capture_until_silence(
    *,
    samplerate: int = _DEFAULT_SAMPLERATE,
    output_path: str = "",
    consent: bool = False,
    silence_hold_seconds: float = _DEFAULT_SILENCE_HOLD_SECONDS,
    start_timeout_seconds: float = _DEFAULT_START_TIMEOUT_SECONDS,
    max_seconds: float | None = None,
    energy_threshold: float = _DEFAULT_ENERGY_THRESHOLD,
) -> AudioCaptureResult:
    """Record one utterance, auto-stopping on a trailing silence (continuous mode).

    Streams the mic via an InputStream, writes frames to a WAV incrementally (bounded
    memory regardless of length), and uses ``vad_should_stop`` to end on silence. Records
    ONLY with ``consent=True``. Honest-blocked when no backend / no speech detected.
    """
    if not consent:
        return AudioCaptureResult(ok=False, blocked_reason="Microphone capture requires explicit consent.", reason="no_consent")
    backend = capture_backend()
    if not backend:
        return AudioCaptureResult(ok=False, backend="", blocked_reason=audio_io_readiness()["blocked_reason"])
    out = str(output_path or "").strip()
    if not out:
        fd, out = tempfile.mkstemp(prefix="chaseos_mic_", suffix=".wav")
        os.close(fd)
    ceiling = _max_capture_ceiling(max_seconds)
    state: dict = {}
    stop_reason: list[str] = []
    lock = threading.Lock()
    try:
        import numpy as np  # noqa: F401 - sounddevice array path
        import sounddevice as sd

        wav = _open_wav(out, samplerate)

        def _callback(indata, frames, time_info, status):  # noqa: ANN001 - sd contract
            with lock:
                if stop_reason:
                    return
                wav.writeframes(bytes(indata))
                reason = vad_should_stop(
                    state, rms=block_rms(indata), now=time.monotonic(),
                    silence_hold_seconds=silence_hold_seconds,
                    start_timeout_seconds=start_timeout_seconds,
                    max_seconds=ceiling, energy_threshold=energy_threshold,
                )
                if reason:
                    stop_reason.append(reason)

        with sd.InputStream(samplerate=samplerate, channels=1, dtype="int16", callback=_callback):
            deadline = time.monotonic() + (ceiling if ceiling and ceiling > 0 else 3600.0) + 5.0
            while not stop_reason and time.monotonic() < deadline:
                time.sleep(0.05)
        wav.close()
    except Exception as exc:  # noqa: BLE001 - bounded
        return AudioCaptureResult(ok=False, backend=backend, reason="error",
                                  blocked_reason=f"Microphone capture failed: {type(exc).__name__}.")

    reason = stop_reason[0] if stop_reason else "max"
    speech = bool(state.get("speech_started"))
    produced = Path(out)
    if not speech or not produced.is_file() or produced.stat().st_size == 0:
        return AudioCaptureResult(ok=False, backend=backend, reason=reason or "start_timeout",
                                  speech_detected=speech, audio_path=str(produced) if produced.is_file() else None,
                                  blocked_reason="No speech detected." if not speech else "Capture produced no audio.")
    return AudioCaptureResult(
        ok=True, audio_path=str(produced), samplerate=samplerate, backend=backend,
        reason=reason, speech_detected=True,
        authority=VoiceAuthority(microphone_capture_performed=True, audio_file_written=True),
    )


# ── open-ended capture sessions (record until the operator stops) ────────────
# Push-to-talk / click-to-toggle voice notes run for ANY length: start_capture opens a
# stream that writes to a WAV incrementally; stop_capture ends it. A configurable safety
# ceiling auto-stops a forgotten recording (CHASEOS_VOICE_MAX_CAPTURE_SECONDS; 0 = unlimited).

_SESSIONS: dict[str, dict] = {}
_SESSIONS_LOCK = threading.Lock()


def start_capture(
    *,
    samplerate: int = _DEFAULT_SAMPLERATE,
    consent: bool = False,
    output_path: str = "",
    max_seconds: float | None = None,
) -> AudioCaptureResult:
    """Begin an open-ended capture session. Returns a session_id; call stop_capture to finish."""
    if not consent:
        return AudioCaptureResult(ok=False, blocked_reason="Microphone capture requires explicit consent.", reason="no_consent")
    backend = capture_backend()
    if not backend:
        return AudioCaptureResult(ok=False, backend="", blocked_reason=audio_io_readiness()["blocked_reason"])
    out = str(output_path or "").strip()
    if not out:
        fd, out = tempfile.mkstemp(prefix="chaseos_mic_", suffix=".wav")
        os.close(fd)
    ceiling = _max_capture_ceiling(max_seconds)
    try:
        import numpy as np  # noqa: F401
        import sounddevice as sd

        wav = _open_wav(out, samplerate)
        started_at = time.monotonic()
        over_ceiling: list[bool] = []

        def _callback(indata, frames, time_info, status):  # noqa: ANN001 - sd contract
            wav.writeframes(bytes(indata))
            if ceiling and ceiling > 0 and (time.monotonic() - started_at) >= ceiling:
                over_ceiling.append(True)
                raise sd.CallbackStop()

        stream = sd.InputStream(samplerate=samplerate, channels=1, dtype="int16", callback=_callback)
        stream.start()
    except Exception as exc:  # noqa: BLE001 - bounded
        return AudioCaptureResult(ok=False, backend=backend, reason="error",
                                  blocked_reason=f"Microphone capture failed to start: {type(exc).__name__}.")
    session_id = "miccap-" + uuid.uuid4().hex[:12]
    with _SESSIONS_LOCK:
        _SESSIONS[session_id] = {"stream": stream, "wav": wav, "path": out, "samplerate": samplerate, "started_at": started_at}
    return AudioCaptureResult(ok=True, session_id=session_id, samplerate=samplerate, backend=backend, reason="recording",
                              authority=VoiceAuthority(microphone_capture_performed=True))


def stop_capture(session_id: str) -> AudioCaptureResult:
    """End an open-ended capture session and return the recorded WAV."""
    with _SESSIONS_LOCK:
        session = _SESSIONS.pop(str(session_id or ""), None)
    if session is None:
        return AudioCaptureResult(ok=False, reason="no_session", blocked_reason=f"No active capture session: {session_id}")
    try:
        try:
            session["stream"].stop()
            session["stream"].close()
        finally:
            session["wav"].close()
    except Exception as exc:  # noqa: BLE001 - bounded
        return AudioCaptureResult(ok=False, reason="error", blocked_reason=f"Capture stop failed: {type(exc).__name__}.")
    produced = Path(session["path"])
    if not produced.is_file() or produced.stat().st_size == 0:
        return AudioCaptureResult(ok=False, reason="empty", blocked_reason="Capture produced no audio.")
    seconds = max(0.0, time.monotonic() - float(session.get("started_at") or 0.0))
    return AudioCaptureResult(
        ok=True, audio_path=str(produced), seconds=round(seconds, 2),
        samplerate=int(session.get("samplerate") or _DEFAULT_SAMPLERATE), backend=capture_backend(), reason="stopped",
        authority=VoiceAuthority(microphone_capture_performed=True, audio_file_written=True),
    )


def active_capture_session_count() -> int:
    with _SESSIONS_LOCK:
        return len(_SESSIONS)


# ── playback ─────────────────────────────────────────────────────────────────

def _no_window_kwargs() -> dict:
    if os.name != "nt":
        return {}
    return {"creationflags": int(getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0)}


def play_audio(path: str) -> AudioPlaybackResult:
    """Play a local WAV file through the OS. Honest-blocked when no backend/file."""
    backend = playback_backend()
    if not backend:
        return AudioPlaybackResult(ok=False, backend="", blocked_reason=audio_io_readiness()["blocked_reason"])
    audio_path = Path(str(path or "").strip())
    if not audio_path.is_file():
        return AudioPlaybackResult(ok=False, backend=backend, blocked_reason=f"Audio file not found: {path}")
    try:
        if backend == "winsound":
            import winsound

            winsound.PlaySound(str(audio_path), winsound.SND_FILENAME)
        else:
            subprocess.run([backend, str(audio_path)], timeout=300, capture_output=True, **_no_window_kwargs())
    except Exception as exc:  # noqa: BLE001 - bounded
        return AudioPlaybackResult(ok=False, backend=backend,
                                   blocked_reason=f"Audio playback failed: {type(exc).__name__}.")
    return AudioPlaybackResult(
        ok=True,
        audio_path=str(audio_path),
        backend=backend,
        authority=VoiceAuthority(audio_played=True),
    )
