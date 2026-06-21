"""piper-local TTS adapter — open-source, on-device, no API key.

Uses the Piper neural TTS CLI when it is installed and a voice model is
configured. Fully local: no network, no provider credential, privacy-first.
When the binary or model is absent the adapter is honest-blocked.

Env:
- ``CHASEOS_VOICE_PIPER_BIN`` — piper executable (default: resolved via PATH)
- ``CHASEOS_VOICE_PIPER_MODEL`` — path to a ``.onnx`` voice model (required to be live)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from runtime.voice.adapters.base import TTSAdapter
from runtime.voice.models import TTSRequest, TTSResult, VoiceAuthority

_PROVIDER = "piper-local"
_MAX_TEXT_CHARS = 8000


def _piper_binary() -> str:
    configured = str(os.environ.get("CHASEOS_VOICE_PIPER_BIN") or "").strip()
    if configured:
        return configured
    found = shutil.which("piper")
    if found:
        return found
    # Fallback: the `piper-tts` console script installs next to the running interpreter
    # (venv Scripts/bin), which may not be on PATH when Studio runs. Look there too so the
    # pip-installed OpenCore voice stack is auto-discovered without PATH activation.
    exe_dir = Path(sys.executable).parent
    for name in ("piper.exe", "piper"):
        candidate = exe_dir / name
        if candidate.is_file():
            return str(candidate)
    return ""


def _voice_dirs() -> list[Path]:
    """Candidate Piper voice directories, in priority order.

    1. A voice bundled inside a packaged Studio build (PyInstaller MEIPASS/voice/piper).
    2. The conventional OpenCore per-user dir (~/.chaseos/voice/piper).
    So a packaged .exe can ship a voice, while a dev/pip install picks up a downloaded one.
    """
    dirs: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        dirs.append(Path(meipass) / "voice" / "piper")
    dirs.append(Path.home() / ".chaseos" / "voice" / "piper")
    return dirs


def _default_voice_dir() -> Path:
    """Conventional OpenCore Piper voice directory (per-user)."""
    return Path.home() / ".chaseos" / "voice" / "piper"


def _piper_model() -> str:
    configured = str(os.environ.get("CHASEOS_VOICE_PIPER_MODEL") or "").strip()
    if configured:
        return configured
    # Persisted tts_voice (a voice name like 'en_US-lessac-medium', or a path).
    try:
        from runtime.voice.settings import load_voice_settings

        chosen = str(load_voice_settings().get("tts_voice") or "").strip()
    except Exception:  # noqa: BLE001 - fail-open
        chosen = ""
    if chosen:
        as_path = Path(chosen)
        if as_path.is_file():
            return str(as_path)
        for voice_dir in _voice_dirs():
            cand = voice_dir / (chosen if chosen.endswith(".onnx") else chosen + ".onnx")
            if cand.is_file():
                return str(cand)
    # OpenCore convention: else the first .onnx voice found in a candidate dir
    # (bundled build voice first, then the per-user download dir).
    for voice_dir in _voice_dirs():
        if voice_dir.is_dir():
            for onnx in sorted(voice_dir.glob("*.onnx")):
                if onnx.is_file():
                    return str(onnx)
    return ""


def list_piper_voices() -> list[str]:
    """Names of installed Piper voices (``*.onnx`` stems) across candidate dirs, deduped + sorted."""
    names: list[str] = []
    seen: set[str] = set()
    for voice_dir in _voice_dirs():
        if not voice_dir.is_dir():
            continue
        for onnx in sorted(voice_dir.glob("*.onnx")):
            if onnx.is_file() and onnx.stem not in seen:
                seen.add(onnx.stem)
                names.append(onnx.stem)
    return names


def _no_window_kwargs() -> dict:
    if os.name != "nt":
        return {}
    return {"creationflags": int(getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0)}


class PiperLocalTTSAdapter(TTSAdapter):
    provider_id = _PROVIDER
    transport = "local"

    def readiness(self) -> dict:
        binary = _piper_binary()
        model = _piper_model()
        if not binary:
            return {"live": False, "provider_id": self.provider_id,
                    "blocked_reason": ("Piper is not installed. Install the open-source engine and set "
                                       "CHASEOS_VOICE_PIPER_BIN (or put `piper` on PATH). No API key is required.")}
        if not model or not Path(model).is_file():
            return {"live": False, "provider_id": self.provider_id,
                    "blocked_reason": ("No Piper voice model configured. Set CHASEOS_VOICE_PIPER_MODEL to a "
                                       "downloaded .onnx voice model.")}
        return {"live": True, "provider_id": self.provider_id, "blocked_reason": None, "model": model}

    def synthesize(self, request: TTSRequest) -> TTSResult:
        ready = self.readiness()
        if not ready.get("live"):
            return TTSResult(ok=False, provider_id=self.provider_id, bridge="voice_piper_local",
                             blocked_reason=ready.get("blocked_reason"))
        text = str(request.text or "").strip()
        if not text:
            return TTSResult(ok=False, provider_id=self.provider_id, bridge="voice_piper_local",
                             blocked_reason="No text supplied for synthesis.")
        if len(text) > _MAX_TEXT_CHARS:
            text = text[:_MAX_TEXT_CHARS]
        out_path = str(request.output_path or "").strip()
        if not out_path:
            fd, out_path = tempfile.mkstemp(prefix="chaseos_tts_", suffix=".wav")
            os.close(fd)
        cmd = [_piper_binary(), "--model", _piper_model(), "--output_file", out_path]
        # Speaking rate → Piper --length-scale (length = 1/rate; >1 slower, <1 faster).
        try:
            from runtime.voice.settings import effective_speaking_rate

            rate = effective_speaking_rate()
            if rate and abs(rate - 1.0) > 1e-3:
                cmd += ["--length-scale", f"{max(0.1, 1.0 / rate):.3f}"]
        except Exception:  # noqa: BLE001 - rate is best-effort
            pass
        try:
            completed = subprocess.run(
                cmd, input=text, text=True, capture_output=True, timeout=120,
                shell=False, **_no_window_kwargs(),
            )
        except FileNotFoundError:
            return TTSResult(ok=False, provider_id=self.provider_id, bridge="voice_piper_local",
                             blocked_reason="Piper binary not found at runtime.")
        except subprocess.TimeoutExpired:
            return TTSResult(ok=False, provider_id=self.provider_id, bridge="voice_piper_local",
                             blocked_reason="Piper synthesis timed out.")
        except Exception as exc:  # noqa: BLE001 - bounded
            return TTSResult(ok=False, provider_id=self.provider_id, bridge="voice_piper_local",
                             blocked_reason=f"Piper synthesis failed: {type(exc).__name__}.")
        produced = Path(out_path)
        if completed.returncode != 0 or not produced.is_file():
            return TTSResult(ok=False, provider_id=self.provider_id, bridge="voice_piper_local",
                             blocked_reason=f"Piper exited without audio (code {completed.returncode}).")
        size = produced.stat().st_size
        # The local engine genuinely ran and wrote an audio file; record it honestly.
        return TTSResult(
            ok=True,
            audio_path=str(produced),
            audio_bytes=size,
            provider_id=self.provider_id,
            bridge="voice_piper_local",
            authority=VoiceAuthority(tts_provider_called=True, audio_file_written=True),
        )
