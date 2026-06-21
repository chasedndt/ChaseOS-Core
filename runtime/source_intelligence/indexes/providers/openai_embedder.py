"""
openai_embedder.py — SIC Phase 7 Pass 7
Optional OpenAI embedding backend for the ChaseOS Source Intelligence Core.

This is an OPTIONAL external backend.

Requirements (user must supply):
  - openai package: pip install openai
  - OPENAI_API_KEY environment variable set

Design contract:
  - This file is only imported when adapter_name == "openai" in get_embedder()
  - If the openai package is not installed, raises EmbedderError with install instructions
  - If OPENAI_API_KEY is not set, raises EmbedderError with setup instructions
  - Never stores or logs the API key
  - Never writes to the vault or any file
  - The local stub remains valid when this backend is unavailable

ChaseOS architecture note:
  This adapter is subordinate to SIC's local contract.
  It does not redefine file layout, manifest design, or retrieval structure.
  It is a thin I/O layer that produces float vectors — nothing more.
"""

from __future__ import annotations

import os

from ..embedder import EmbedderBase, EmbedderError

# Supported OpenAI embedding models and their output dimensions
_MODEL_DIMENSIONS: dict[str, int] = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}
_DEFAULT_MODEL = "text-embedding-3-small"


class OpenAIEmbedder(EmbedderBase):
    """
    OpenAI embedding backend (optional, user-configured).

    Wraps the OpenAI Embeddings API. Requires:
      - openai package installed (pip install openai)
      - OPENAI_API_KEY environment variable set

    Supported models:
      - text-embedding-3-small (1536 dims) — cost-effective, recommended
      - text-embedding-3-large (3072 dims) — higher quality, higher cost
      - text-embedding-ada-002 (1536 dims) — legacy model

    The local_stub and local_word backends remain valid alternatives that
    require no credentials or API calls.

    Batching: texts are sent in a single API call. For very large batches
    (>100 chunks), callers may want to chunk the input. The index_manager
    calls embed() per source package (typically 5-15 chunks), so this is
    not an issue for normal SIC usage.
    """

    name = "openai"

    def __init__(self, model_name: str = _DEFAULT_MODEL) -> None:
        if model_name not in _MODEL_DIMENSIONS:
            supported = sorted(_MODEL_DIMENSIONS.keys())
            raise EmbedderError(
                f"Unknown OpenAI embedding model '{model_name}'. "
                f"Supported: {supported}"
            )

        self.model_name = model_name
        self.dimension = _MODEL_DIMENSIONS[model_name]

        # Check openai package
        try:
            import openai as _openai_mod
            self._openai = _openai_mod
        except ImportError:
            raise EmbedderError(
                "openai package not installed. "
                "Run: .venv/Scripts/pip install openai\n"
                "The openai backend is optional — local_stub and local_word "
                "work without any packages."
            )

        # Check API key
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise EmbedderError(
                "OPENAI_API_KEY environment variable not set. "
                "Set it to use the OpenAI backend, or use local_stub / local_word "
                "which require no credentials."
            )

        self._client = self._openai.OpenAI(api_key=api_key)

    def embed(self, texts: list[str]) -> list[list[float]]:
        """
        Embed a batch of texts via the OpenAI Embeddings API.

        Returns one vector per input text, in the same order.
        Vectors have self.dimension floats (e.g. 1536 for text-embedding-3-small).

        Raises:
            EmbedderError if the API call fails.
        """
        if not texts:
            raise EmbedderError("texts must be a non-empty list.")

        try:
            response = self._client.embeddings.create(
                input=texts,
                model=self.model_name,
            )
        except Exception as exc:
            raise EmbedderError(
                f"OpenAI embedding API call failed: {exc}. "
                "Check OPENAI_API_KEY, network connectivity, and quota."
            ) from exc

        if len(response.data) != len(texts):
            raise EmbedderError(
                f"OpenAI returned {len(response.data)} embeddings for "
                f"{len(texts)} inputs. Length mismatch."
            )

        # API returns items in input order, but sort by index for safety
        items = sorted(response.data, key=lambda x: x.index)
        return [item.embedding for item in items]


def check_availability() -> dict:
    """
    Check whether the OpenAI backend is available in the current environment.

    Returns:
        dict with keys:
            available   — bool: True if the backend can be instantiated
            reason      — str: why it is or isn't available
            model       — str: default model name
            dimension   — int: default model dimension
    """
    # Check package
    try:
        import openai  # noqa: F401
    except ImportError:
        return {
            "available": False,
            "reason": "openai package not installed (pip install openai)",
            "model": _DEFAULT_MODEL,
            "dimension": _MODEL_DIMENSIONS[_DEFAULT_MODEL],
        }

    # Check API key
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return {
            "available": False,
            "reason": "OPENAI_API_KEY environment variable not set",
            "model": _DEFAULT_MODEL,
            "dimension": _MODEL_DIMENSIONS[_DEFAULT_MODEL],
        }

    return {
        "available": True,
        "reason": "openai package installed and OPENAI_API_KEY is set",
        "model": _DEFAULT_MODEL,
        "dimension": _MODEL_DIMENSIONS[_DEFAULT_MODEL],
    }
