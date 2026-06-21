"""
embedder.py — SIC Phase 7 Pass 4 / Pass 7
Embedding seam for the ChaseOS Source Intelligence Core.

This module defines:
  - EmbedderBase: the interface all embedders must satisfy
  - LocalStubEmbedder: deterministic hash-based stub (default, no deps)
  - LocalWordEmbedder: word frequency hash projection (lexical signal, no deps)
  - get_embedder(): factory function resolving backend by name

Pass 4 history:
  - EmbedderBase + LocalStubEmbedder established
  - index_manager depends only on EmbedderBase

Pass 7 additions:
  - LocalWordEmbedder: upgraded local-first backend using the feature hashing trick.
    Produces lexically meaningful vectors without any external dependencies.
    Texts sharing vocabulary produce more similar vectors than the hash stub.
  - OpenAI backend: delegated to providers/openai_embedder.py (opt-in).
    Requires: pip install openai AND OPENAI_API_KEY env var.
    Not available by default. Local stub remains the fallback if unavailable.
  - get_embedder() now handles per-backend default dimensions.

Backend priority:
  1. local_stub  — deterministic hash, no semantic signal, no deps (default fallback)
  2. local_word  — word frequency hash projection, lexical signal, no deps
  3. openai      — full semantic embeddings, opt-in, user-supplied credentials

Design constraint:
  This module owns the embedding interface only.
  It does NOT own workspace logic, source package schema, or writeback rules.
  Those remain in workspace_manager and source_package_builder respectively.
"""

from __future__ import annotations

import hashlib
import math
import re
import struct
from abc import ABC, abstractmethod


# ── Interface ─────────────────────────────────────────────────────────────────

class EmbedderBase(ABC):
    """
    Minimal contract every embedder must satisfy.

    embed() receives a list of text strings and returns a list of float vectors.
    One vector per input text, in the same order. Each vector has exactly
    self.dimension floats.
    """

    name: str           # short identifier, e.g. "local_stub", "local_word", "openai"
    model_name: str     # model identifier, e.g. "local-test-embedding-v1"
    dimension: int      # vector dimension — fixed per model

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """
        Embed a batch of text strings.

        Args:
            texts: Non-empty list of text strings.

        Returns:
            List of float vectors, one per input text. Length == len(texts).
            Each vector has exactly self.dimension floats.

        Raises:
            EmbedderError on any failure.
        """
        ...


# ── Local stub embedder ───────────────────────────────────────────────────────

class LocalStubEmbedder(EmbedderBase):
    """
    Deterministic hash-based stub embedder.

    NOT FOR PRODUCTION. NOT SEMANTICALLY MEANINGFUL.

    Purpose:
    - Exercises the full index contract and state machine
    - Produces stable, reproducible vectors for testing
    - Requires no external dependencies or credentials

    Algorithm:
    - SHA-256 hash of the UTF-8 encoded text
    - Extend hash by re-hashing with a counter suffix until we have enough bytes
    - Interpret pairs of bytes as signed int16, normalize to [-1.0, 1.0]
    - Produces a fixed-dimension float vector

    The vectors are deterministic for the same input text and dimension.
    They carry NO semantic meaning — texts with similar content do not produce
    more similar vectors than texts with different content.

    Use LocalWordEmbedder if you want lexical signal without external deps.
    """

    name = "local_stub"
    model_name = "local-test-embedding-v1"

    def __init__(self, dimension: int = 64):
        if dimension < 8 or dimension > 4096:
            raise ValueError(f"dimension must be between 8 and 4096, got {dimension}")
        self.dimension = dimension

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            raise EmbedderError("texts must be a non-empty list.")
        return [self._embed_one(t) for t in texts]

    def _embed_one(self, text: str) -> list[float]:
        """Produce a deterministic float vector from a text string."""
        needed_bytes = self.dimension * 2  # 2 bytes per float (int16)
        raw = _hash_bytes(text.encode("utf-8"), needed_bytes)
        # Unpack as signed int16 values
        shorts = struct.unpack(f">{self.dimension}h", raw[:needed_bytes])
        # Normalize to [-1.0, 1.0]
        return [s / 32767.0 for s in shorts]


def _hash_bytes(seed: bytes, n_bytes: int) -> bytes:
    """Produce n_bytes of deterministic pseudo-random bytes from seed."""
    result = b""
    counter = 0
    while len(result) < n_bytes:
        h = hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
        result += h
        counter += 1
    return result[:n_bytes]


# ── Local word embedder ───────────────────────────────────────────────────────

class LocalWordEmbedder(EmbedderBase):
    """
    Word frequency hash projection embedder (feature hashing trick).

    IMPROVED LOCAL-FIRST OPTION. Lexically meaningful. No external dependencies.

    Produces vectors where texts sharing vocabulary produce MORE SIMILAR vectors
    than texts with different vocabulary. This is a real improvement over
    LocalStubEmbedder which treats all texts as equally random.

    Algorithm (feature hashing / "hashing trick"):
    1. Lowercase and tokenize the text (split on non-alphanumeric)
    2. Remove stop words; require token length >= 2
    3. For each token:
       a. Position: SHA-256 hash % dimension → index in vector
       b. Sign:     MD5 hash % 2 → +1 or -1
       c. Accumulate: vec[position] += sign
    4. L2 normalize the resulting vector

    Properties:
    - Deterministic: same text always produces the same vector
    - Lexically meaningful: shared vocabulary → higher cosine similarity
    - No corpus needed: each text is embedded independently
    - No external dependencies: uses only stdlib (hashlib, math, re)
    - Default dimension: 256 (higher than stub to reduce collision rate)

    Limitations:
    - No semantic meaning beyond vocabulary overlap
    - Synonyms (e.g., "trading" vs "investing") are not related
    - For full semantic similarity, use the openai backend
    """

    name = "local_word"
    model_name = "local-word-hash-v1"

    # Minimal English stop words — common words that carry no topical signal
    _STOP_WORDS: frozenset[str] = frozenset({
        "a", "an", "the", "and", "or", "but", "in", "on", "at", "to",
        "for", "of", "with", "by", "is", "are", "was", "were", "be",
        "been", "being", "have", "has", "had", "do", "does", "did",
        "will", "would", "could", "should", "may", "might", "shall",
        "this", "that", "these", "those", "it", "its", "not", "no",
        "as", "if", "so", "we", "i", "you", "he", "she", "they",
        "also", "from", "more", "than", "can", "all", "one", "two",
        "how", "what", "when", "where", "which", "who", "why", "any",
        "each", "both", "about", "up", "out", "into", "through",
        "then", "there", "here", "just", "only", "very", "well",
        "get", "use", "used", "using", "like", "over", "after",
        "before", "between", "while", "during", "some", "such",
    })

    def __init__(self, dimension: int = 256):
        if dimension < 16 or dimension > 4096:
            raise ValueError(f"dimension must be between 16 and 4096, got {dimension}")
        self.dimension = dimension

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            raise EmbedderError("texts must be a non-empty list.")
        return [self._embed_one(t) for t in texts]

    def _embed_one(self, text: str) -> list[float]:
        """
        Produce a word-frequency projection vector.

        Each token is hashed to a position and sign in the output vector.
        Repeated tokens accumulate in the same bucket (counting).
        """
        # Tokenize: lowercase, split on non-alphanumeric, filter
        raw_tokens = re.split(r"[^a-z0-9]+", text.lower())
        tokens = [
            t for t in raw_tokens
            if t and len(t) >= 2 and t not in self._STOP_WORDS
        ]

        if not tokens:
            # Empty text or all stop words — return zero vector
            return [0.0] * self.dimension

        vec = [0.0] * self.dimension

        for token in tokens:
            tok_bytes = token.encode("utf-8")

            # Position hash: which bucket does this token go into?
            pos_hash = int(hashlib.sha256(b"pos:" + tok_bytes).hexdigest(), 16)
            position = pos_hash % self.dimension

            # Sign hash: +1 or -1 (reduces systematic collision bias)
            sign_hash = int(hashlib.md5(b"sgn:" + tok_bytes).hexdigest(), 16)
            sign = 1 if sign_hash % 2 == 0 else -1

            vec[position] += sign

        # L2 normalize so cosine similarity works correctly
        mag = math.sqrt(sum(v * v for v in vec))
        if mag == 0.0:
            return vec
        return [v / mag for v in vec]


# ── Factory ───────────────────────────────────────────────────────────────────

# Default dimensions per backend
_BACKEND_DEFAULTS: dict[str, dict] = {
    "local_stub": {"dimension": 64,   "cls": LocalStubEmbedder},
    "local_word": {"dimension": 256,  "cls": LocalWordEmbedder},
}

# OpenAI model → dimension mapping (canonical)
_OPENAI_MODEL_DIMENSIONS: dict[str, int] = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}
_OPENAI_DEFAULT_MODEL = "text-embedding-3-small"


def get_embedder(
    adapter_name: str = "local_stub",
    model_name: str | None = None,
    dimension: int | None = None,
) -> EmbedderBase:
    """
    Return an embedder instance for the given adapter_name.

    Pass 7: Supports local_stub, local_word, and openai backends.

    Args:
        adapter_name: "local_stub" | "local_word" | "openai"
        model_name:   Model override. Used for openai to select the model.
                      Ignored for local backends (they have fixed model names).
        dimension:    Embedding dimension override.
                      For local backends: overrides the backend default.
                      For openai: ignored (dimension is determined by the model).
                      Pass None to use the backend default.

    Returns:
        An EmbedderBase instance.

    Raises:
        EmbedderError if adapter_name is not registered or backend is unavailable.
    """
    if adapter_name in _BACKEND_DEFAULTS:
        info = _BACKEND_DEFAULTS[adapter_name]
        dim = dimension if dimension is not None else info["dimension"]
        return info["cls"](dimension=dim)

    if adapter_name == "openai":
        # Delegate to optional OpenAI provider
        try:
            from .providers.openai_embedder import OpenAIEmbedder
            effective_model = model_name or _OPENAI_DEFAULT_MODEL
            return OpenAIEmbedder(model_name=effective_model)
        except ImportError as exc:
            raise EmbedderError(
                f"OpenAI provider module not found: {exc}. "
                "This is a SIC internal error."
            ) from exc
        except EmbedderError:
            raise  # re-raise provider-level errors (missing package, missing API key)

    registered = sorted(list(_BACKEND_DEFAULTS.keys()) + ["openai"])
    raise EmbedderError(
        f"Unknown adapter '{adapter_name}'. Registered: {registered}. "
        "To add a new provider, implement EmbedderBase and register it in embedder.py."
    )


def list_adapter_names() -> list[str]:
    """Return all registered adapter names (including external backends)."""
    return sorted(list(_BACKEND_DEFAULTS.keys()) + ["openai"])


# ── Exceptions ────────────────────────────────────────────────────────────────

class EmbedderError(Exception):
    pass
