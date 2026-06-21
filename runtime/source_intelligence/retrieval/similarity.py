"""
similarity.py — SIC Phase 7 Pass 5
Cosine similarity utilities for the local retrieval layer.

Local-first. No external dependencies beyond the Python standard library.
All functions are deterministic and stateless.

Design note on cosine similarity:
    Cosine similarity measures the angle between two vectors, not their
    magnitude. It is the standard choice for semantic retrieval because
    it is scale-invariant — a long and a short text that express the same
    idea can produce vectors pointing in the same direction.

    For the local stub embedder (hash-based), cosine similarity does not
    carry semantic meaning. It will still produce stable, deterministic
    rankings that exercise the full retrieval contract.

    When a real embedding provider is wired (Pass 7+), the same cosine
    similarity function applies without modification. No changes needed here.
"""

from __future__ import annotations

import math


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """
    Compute cosine similarity between two float vectors.

    Returns a score in [-1.0, 1.0].
    Returns 0.0 if either vector has zero magnitude (cosine undefined).

    Args:
        vec_a: First vector (e.g. query embedding).
        vec_b: Second vector (e.g. chunk embedding).

    Raises:
        ValueError if the vectors have different dimensions.
    """
    if len(vec_a) != len(vec_b):
        raise ValueError(
            f"Vector dimension mismatch: {len(vec_a)} vs {len(vec_b)}. "
            "Query and chunk vectors must come from the same embedding model."
        )

    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    mag_a = math.sqrt(sum(a * a for a in vec_a))
    mag_b = math.sqrt(sum(b * b for b in vec_b))

    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0

    # Clamp to [-1, 1] to guard against floating point drift
    return max(-1.0, min(1.0, dot / (mag_a * mag_b)))


def rank_chunks(
    query_vector: list[float],
    chunk_vectors: dict[str, list[float]],
) -> list[tuple[str, float]]:
    """
    Score all chunk vectors against a query vector and return ranked results.

    Args:
        query_vector:  Embedded query text.
        chunk_vectors: Mapping of chunk_id → float vector.

    Returns:
        List of (chunk_id, score) tuples sorted descending by score.

        Tie-breaking is deterministic: equal scores are ordered by chunk_id
        lexicographically (ascending). This ensures the same query always
        returns the same ranked list given the same index state.

    Returns an empty list if chunk_vectors is empty.
    """
    if not chunk_vectors:
        return []

    scored = [
        (chunk_id, cosine_similarity(query_vector, vec))
        for chunk_id, vec in chunk_vectors.items()
    ]
    # Primary sort: score descending. Secondary: chunk_id ascending (determinism).
    scored.sort(key=lambda x: (-x[1], x[0]))
    return scored
