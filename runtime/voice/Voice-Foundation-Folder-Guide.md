---
type: folder-guide
domain: voice
status: foundation
created: 2026-06-18
runtime_node: "[[Archon-Runtime-Profile]]"
---

# `runtime/voice/` — Voice (STT/TTS) Foundation Folder Guide

> Modular foundation for ChaseOS Studio voice. **Foundation only — not live.**
> No microphone capture, no audio file writes, no provider calls, no synthesis.

## What this is

Item E of the Agent Control Plane chat work. It is the provider-agnostic seam for
speech-to-text and text-to-speech, built so the whole path is wired end-to-end
and the UI can show the exact blocked reason — without pretending audio works.

It follows the canonical rule in
[[Provider-Agnostic-Routing-Architecture]]: **Studio/UI surfaces never call audio
providers directly.** Voice routes through an adapter layer; each backend owns its
own credentials and model choice. This is the audio analogue of the LLM rule.

## Layout

| File | Role |
|------|------|
| `models.py` | DOM/provider-free dataclasses: `STTRequest/Result`, `TTSRequest/Result`, `VoiceProviderSpec`, `VoiceReadiness`, and the `VoiceAuthority` no-effect flag block. |
| `adapters/base.py` | `STTAdapter` / `TTSAdapter` ABCs — the only place allowed to touch an audio provider. |
| `adapters/null_adapter.py` | Honest no-op adapters (used for disabled lane / unregistered provider). |
| `adapters/local_whisper.py` | **Live** open-source STT — `faster-whisper`, on-device, no API key. Real transcription when installed; honest-blocked otherwise. |
| `adapters/piper_local.py` | **Live** open-source TTS — Piper neural TTS CLI, on-device, no API key. Real synthesis-to-file when installed + a voice model is configured; honest-blocked otherwise. |
| `provider_registry.py` | Declared providers + env-driven resolution. **Defaults to the open-source local engines** (`local-whisper` STT, `piper-local` TTS); falls back to null when the lane is disabled or an unregistered provider is configured. `register_stt_adapter` / `register_tts_adapter` plug in further backends. |
| `audio_io.py` | **OS audio I/O** — mic capture (sounddevice→WAV) + playback (winsound/afplay/aplay/paplay). Multi-backend, availability-detected. **Capture requires `consent=True`** (privacy gate). |
| `pipeline.py` | End-to-end: `transcribe_microphone()` (capture→STT) and `speak_text()` (TTS→playback). Each stage honest-gated; merges authority of whatever genuinely ran. |
| `readiness.py` | `voice_delivery_truth()` — readiness verdict incl. an `audio_io` block + composite `can_transcribe_microphone` / `can_speak`. |
| `tests/` | `test_voice_foundation.py` (10) + `test_voice_audio_io.py` (11) — 21 tests, all backends mocked. |

## Authority boundary (enforced by `VoiceAuthority`, all default False)

`microphone_capture_performed` · `audio_file_written` · `stt_provider_called` ·
`tts_provider_called` · `audio_played` · `canonical_writeback`

The foundation never flips one to True. A future live adapter may set a flag only
after the corresponding gated capability genuinely executes.

## Env

- `CHASEOS_VOICE_STT_PROVIDER` / `CHASEOS_VOICE_TTS_PROVIDER` — preferred provider id
  (defaults: `local-whisper` / `piper-local`).
- `CHASEOS_VOICE_DISABLED` — hard off-switch for the whole lane.
- `CHASEOS_VOICE_WHISPER_MODEL` / `_DEVICE` / `_COMPUTE` — faster-whisper config (default `base` / `cpu` / `int8`).
- `CHASEOS_VOICE_PIPER_BIN` / `CHASEOS_VOICE_PIPER_MODEL` — Piper binary + `.onnx` voice model.

## Enabling the open-source engines (no API key, on-device) — ChaseOS OpenCore

The voice stack ships as the `voice` optional-dependency extra in `pyproject.toml`:

```
pip install -e ".[voice]"
# installs: faster-whisper (STT) · piper-tts (TTS) · sounddevice (capture) · numpy
```

After install (verified on Python 3.14, wheels only — no source build):
- **STT** (`faster-whisper`) is live immediately; the Whisper model auto-downloads on first
  transcription (`CHASEOS_VOICE_WHISPER_MODEL`, default `base`).
- **Capture** (`sounddevice`) + **Playback** (`winsound`/`afplay`/`aplay`) are live.
- **TTS** (`piper-tts`) — the `piper` binary is auto-discovered next to the interpreter
  (venv `Scripts/`), so no PATH activation is needed. It needs a voice `.onnx`:
  - Download one into the conventional dir and it's picked up automatically (no env var):
    `python -m piper.download_voices en_US-lessac-medium --download-dir ~/.chaseos/voice/piper`
  - Or point `CHASEOS_VOICE_PIPER_MODEL` at a specific `.onnx`.

`voice_delivery_truth()` reports the exact remaining step (e.g. "download a Piper voice")
until each lane is live.

## Packaging into the Studio .exe (OpenCore)

Voice bundling is **build-time opt-in** so the default Studio `.exe` stays lean (the voice
stack adds heavy native libs: ctranslate2 / onnxruntime / av):

```
# Voice-enabled build:
runtime/studio/shell/build_exe.ps1 -Voice [-VoiceModel en_US-lessac-medium]
```

`-Voice` installs the voice extra, ensures a Piper voice in `~/.chaseos/voice/piper/`, **stages
the CTranslate2 Whisper STT model** in `~/.chaseos/voice/whisper/<size>/`, and sets
`CHASEOS_STUDIO_BUNDLE_VOICE=1`. The PyInstaller spec (`ChaseOS-Studio.spec`) then collects
faster-whisper + Piper + sounddevice (+ numpy, normally excluded) and bundles the `piper`
binary, the Piper voice under `voice/piper`, and the Whisper model under `voice/whisper`.
At runtime the adapters resolve the bundled assets via `sys._MEIPASS` (`_voice_dirs()` for
Piper, `_whisper_dirs()` for Whisper).

**Fully offline / air-gapped (Option B):** with `-Voice`, STT is air-gapped too — the Whisper
model loads from the staged/bundled dir, so the first transcription needs no network (verified
with `HF_HUB_OFFLINE=1`). `voice_delivery_truth` / the STT readiness reports
`model_source: bundled-offline` vs `download-on-first-use`.

## Studio surface (voice lives in Chat — no standalone panel)

The standalone "Voice Mode" panel was removed; voice is now **in the chat composer** plus a
**Voice settings card**. Persisted settings (`settings.py`) drive behavior; env overrides.

Read-only readiness:
- `StudioAPI.get_voice_readiness(probe=False)` → `voice_delivery_truth`
- `StudioAPI.get_voice_audio_io_readiness()` → mic/playback backends

Settings:
- `StudioAPI.get_voice_settings()` → settings + installed TTS voices + readiness
- `StudioAPI.set_voice_settings(patch)` → persist (validated/clamped)

Consent-gated capture/synthesis (no fixed length cap; VAD-driven for hands-free):
- `StudioAPI.voice_start_capture(consent)` / `voice_stop_capture(session_id, transcribe, language)`
  — open-ended push-to-talk / toggle note (records until you stop).
- `StudioAPI.voice_listen_once(consent, language)` — one VAD utterance (continuous mode).
- `StudioAPI.voice_speak_text(text, play)` — TTS + playback.

Frontend (isolated, self-wiring modules — survive app.js/styles.css overwrites):
- `frontend/chatVoice.js` — repurposes the composer **Voice** button: push-to-talk (hold,
  default), toggle, or hands-free continuous (VAD listen → transcribe → auto-send → speak →
  loop). Auto-speaks new runtime replies via a MutationObserver when `auto_speak` is on.
  Consent-gated.
- `frontend/voiceSettings.js` — renders + wires the `#settings-voice` card (master enable,
  auto-speak, hands-free, mic mode, STT size + language, TTS voice + speaking rate, consent,
  end-of-speech pause, max note length) with inline voice-doctor readiness.

## How to make voice live later (the plug-in path)

1. Implement an `STTAdapter` / `TTSAdapter` subclass for the chosen backend
   (bus-routed via Hermes audio API, on-device whisper/piper, or a direct
   provider the backend owns).
2. Set its `VoiceAuthority` flags honestly inside the adapter.
3. `register_stt_adapter(provider_id, cls)` / `register_tts_adapter(...)` and flip
   `implemented=True` on its `VoiceProviderSpec`.
4. Gate any real capture/synthesis/provider call behind explicit approval.
5. No Studio code changes — resolution is registry + env driven.

## Built so far

- The provider-agnostic seam (ABCs, registry, readiness, authority flags).
- **Live open-source STT/TTS adapters** (faster-whisper, Piper) — real
  transcription/synthesis when the engine is installed, honest-blocked otherwise.
- **OS audio I/O** — mic capture (consent-gated) + playback, multi-backend.
- **End-to-end pipelines** — `transcribe_microphone()` and `speak_text()`.
- Read-only `StudioAPI.get_voice_readiness()` + `get_voice_audio_io_readiness()`.

The full chain now exists backend-side: mic → STT → text, and text → TTS → speaker.
Each stage reports honestly and only flips its authority flag on a genuine run.

## Not built (deferred, by design — each separately gated)

- A streaming/partial-transcript mode for the backend STT (current STT is whole-file;
  the browser fallback already streams partials).
- Bus-routed Hermes audio adapter (gateway `audio_api: false` today) and direct
  cloud-provider adapters (openai-tts/whisper, elevenlabs).
- A live, on-device end-to-end demo on this host requires installing the open-source
  engines (`pip install faster-whisper sounddevice numpy` + a Piper binary/voice); until
  then the panel + readiness honestly report the exact install step.
