"""
backend_registry.py — SIC Phase 7 Pass 7
Backend descriptor and availability registry for the ChaseOS Source Intelligence Core.

This module provides a single authoritative place to:
  - List all known embedding backends
  - Check which backends are available in the current environment
  - Get metadata (label, dimension, quality level, deps) for each backend

Used by:
  - index_manager.py (list-backends CLI command)
  - benchmark.py (backend enumeration for comparison)
  - Any code that needs to know the default dimension for a backend

Design notes:
  - The registry is a read-only descriptor store. It does not instantiate embedders.
  - Availability checks are lazy (called on demand, not at import time).
  - Adding a new backend requires updating this file and embedder.py.
  - "semantic_quality" is an engineering characterization, not a benchmark claim:
      none     — vectors carry no semantic or lexical signal (stub hash)
      lexical  — vectors reflect vocabulary overlap (word hash)
      semantic — vectors reflect meaning similarity (trained embedding model)
"""

from __future__ import annotations

# ── Backend descriptors ────────────────────────────────────────────────────────

# This is the authoritative list of all known SIC embedding backends.
# Ordered by preference: local-first, external last.
BACKEND_DESCRIPTORS: dict[str, dict] = {
    "local_stub": {
        "name":              "local_stub",
        "label":             "Local Stub (deterministic hash)",
        "default_model":     "local-test-embedding-v1",
        "default_dimension": 64,
        "semantic_quality":  "none",
        "requires_package":  None,
        "requires_env":      None,
        "local_first":       True,
        "always_available":  True,
        "notes":             (
            "SHA-256 hash-based stub. Deterministic, reproducible, no deps. "
            "Vectors carry NO semantic or lexical signal. For testing only."
        ),
    },
    "local_word": {
        "name":              "local_word",
        "label":             "Local Word Hash (lexical projection)",
        "default_model":     "local-word-hash-v1",
        "default_dimension": 256,
        "semantic_quality":  "lexical",
        "requires_package":  None,
        "requires_env":      None,
        "local_first":       True,
        "always_available":  True,
        "notes":             (
            "Feature hashing over word tokens. No external dependencies. "
            "Texts sharing vocabulary produce more similar vectors. "
            "Better than stub for real queries; no cross-word semantic understanding."
        ),
    },
    "openai": {
        "name":              "openai",
        "label":             "OpenAI Embeddings (optional external)",
        "default_model":     "text-embedding-3-small",
        "default_dimension": 1536,
        "semantic_quality":  "semantic",
        "requires_package":  "openai",
        "requires_env":      "OPENAI_API_KEY",
        "local_first":       False,
        "always_available":  False,
        "notes":             (
            "Full semantic embeddings via OpenAI API. Requires 'openai' package "
            "and OPENAI_API_KEY. User data sent to external service. "
            "Opt-in only — local_stub and local_word work without any credentials."
        ),
    },
}


# ── Availability check ─────────────────────────────────────────────────────────

def check_backend_availability(backend_name: str) -> dict:
    """
    Check whether a backend is available in the current environment.

    Returns:
        dict with keys:
            name        — backend name
            available   — bool
            reason      — why it is or isn't available
            label       — human-readable label
            default_model     — default model identifier
            default_dimension — default vector dimension
            semantic_quality  — "none" | "lexical" | "semantic"
            local_first       — bool
            notes             — usage notes
    """
    info = BACKEND_DESCRIPTORS.get(backend_name)
    if info is None:
        return {
            "name":             backend_name,
            "available":        False,
            "reason":           f"Unknown backend '{backend_name}'.",
            "label":            "unknown",
            "default_model":    None,
            "default_dimension": None,
            "semantic_quality": None,
            "local_first":      False,
            "notes":            None,
        }

    base = dict(info)

    if info.get("always_available"):
        base["available"] = True
        base["reason"]    = "No external dependencies required."
        return base

    # Check package
    if info.get("requires_package"):
        try:
            __import__(info["requires_package"])
        except ImportError:
            base["available"] = False
            base["reason"] = (
                f"Package '{info['requires_package']}' not installed. "
                f"Run: .venv/Scripts/pip install {info['requires_package']}"
            )
            return base

    # Check environment variable
    if info.get("requires_env"):
        import os
        if not os.environ.get(info["requires_env"]):
            base["available"] = False
            base["reason"] = (
                f"Environment variable '{info['requires_env']}' not set."
            )
            return base

    base["available"] = True
    base["reason"] = "Package installed and required environment variables are set."
    return base


def list_backends(check_availability: bool = True) -> list[dict]:
    """
    Return descriptors for all known backends.

    Args:
        check_availability: If True, include real-time availability status.
                            If False, include only static descriptor info.

    Returns:
        List of backend descriptor dicts, ordered by preference (local-first).
    """
    results = []
    for name in BACKEND_DESCRIPTORS:
        if check_availability:
            results.append(check_backend_availability(name))
        else:
            results.append(dict(BACKEND_DESCRIPTORS[name]))
    return results


def get_backend_default_dimension(backend_name: str) -> int | None:
    """Return the default dimension for a backend, or None if unknown."""
    info = BACKEND_DESCRIPTORS.get(backend_name)
    if info is None:
        return None
    return info.get("default_dimension")


def get_backend_default_model(backend_name: str) -> str | None:
    """Return the default model identifier for a backend, or None if unknown."""
    info = BACKEND_DESCRIPTORS.get(backend_name)
    if info is None:
        return None
    return info.get("default_model")
