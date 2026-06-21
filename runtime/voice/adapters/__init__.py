"""Voice adapters — the provider-agnostic STT/TTS seam.

``base`` defines the ABCs; ``null_adapter`` provides the honest no-op default.
Live provider adapters (whisper/openai-tts/elevenlabs/piper/hermes-audio) plug
in here later, each owning its own credentials — Studio never imports them.
"""

from runtime.voice.adapters.base import STTAdapter, TTSAdapter
from runtime.voice.adapters.null_adapter import NullSTTAdapter, NullTTSAdapter

__all__ = ["STTAdapter", "TTSAdapter", "NullSTTAdapter", "NullTTSAdapter"]
