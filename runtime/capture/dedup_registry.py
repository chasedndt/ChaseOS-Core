"""
dedup_registry.py — ChaseOS Phase 8 Pass 6
SHA-256 deduplication registry for the Connector / Capture layer.

REGISTRY LOCATION:
    <vault_root>/.chaseos/dedup_registry.json

    The `.chaseos/` directory is the ChaseOS tool-state directory.
    It stores operational state that belongs to the vault but is not user content.
    Convention mirrors `.git/`, `.venv/`, etc.

DEDUP IDENTITY RULE:
    Primary key: SHA-256 hex digest of the normalized content body (UTF-8 encoded).

    Rationale:
        - Content is what we are deduplicating: if the text is identical, it
          is a duplicate regardless of capture time, source, or connector.
        - SHA-256 is already computed by intake_writer.py — no extra cost.
        - Source URL alone is insufficient (content changes at the same URL).
        - Title alone is insufficient (different articles may share a title).
        - The content hash is the strongest available signal with no external deps.

    Consequence:
        - Same article captured twice → duplicate (same hash).
        - Same article with updated description → new hash → not a duplicate.
        - Metadata-only differences (different event_date_hint, different workspace)
          do not constitute a duplicate; content must differ for a new capture.

    This is the correct rule for Phase 8. Future passes may layer supplementary
    dedup signals (e.g. source_url + source_platform) if hash-only proves too strict.

REGISTRY FORMAT:
    {
      "schema_version": "1.0",
      "entries": {
        "<sha256_hex>": {
          "content_sha256":   "<hex>",
          "capture_id":       "<UUID4>",
          "first_captured_at":"<ISO 8601 UTC>",
          "title":            "<str>",
          "source_platform":  "<str>",
          "source_url":       "<str | null>",
          "input_class":      "<str>",
          "capture_method":   "<str>"
        },
        ...
      }
    }

CONNECTOR-AGNOSTIC DESIGN:
    This module has no knowledge of RSS, CLI, or any specific connector.
    It is wired into capture_content() in capture.py — the single public API
    for all connectors. Any connector that writes through capture_content()
    automatically benefits from dedup protection.

GOVERNANCE:
    - Registry is append-only at the API level (no deletion function).
    - Registry does not govern promotion. It is a quarantine-layer guard only.
    - Registry does not trigger SIC.
    - Duplicate detection does NOT mutate canonical state.
    - If registry is corrupted or missing, capture proceeds without dedup (fail-open).

THREAD SAFETY:
    Single-file JSON. Not safe for concurrent writers.
    Acceptable for Phase 8 — solo-operator, sequential CLI invocations.
    Future: file locking if concurrent captures ever needed.

INSPECTABILITY:
    - Registry file is human-readable JSON at .chaseos/dedup_registry.json.
    - `chaseos intake dedup-stats` shows entry count and registry path.
    - Each entry stores enough provenance to understand the first capture.
"""

from __future__ import annotations

import json
from pathlib import Path


# ── Registry constants ─────────────────────────────────────────────────────────

REGISTRY_SCHEMA_VERSION = "1.0"
_CHASEOS_STATE_DIR      = ".chaseos"
_REGISTRY_FILENAME      = "dedup_registry.json"


# ── Path helpers ───────────────────────────────────────────────────────────────

def registry_path(vault_root: Path) -> Path:
    """
    Return the absolute path to the dedup registry file.

    Path: <vault_root>/.chaseos/dedup_registry.json
    """
    return vault_root / _CHASEOS_STATE_DIR / _REGISTRY_FILENAME


# ── Registry I/O ───────────────────────────────────────────────────────────────

def load_registry(vault_root: Path) -> dict:
    """
    Load the dedup registry from disk.

    Returns an empty registry dict if the file does not exist.
    If the file is present but unreadable, returns an empty registry (fail-open)
    so that captures are not blocked by a corrupt registry.

    Returns:
        dict with keys: "schema_version", "entries"
    """
    path = registry_path(vault_root)
    if not path.exists():
        return _empty_registry()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        # Fail-open: corrupt registry → treat as empty; capture proceeds normally
        return _empty_registry()


def save_registry(vault_root: Path, registry: dict) -> None:
    """
    Persist the registry dict to disk.

    Creates .chaseos/ directory if it does not exist.
    Writes atomically via a temporary file is not implemented in Phase 8 —
    acceptable for sequential solo-operator use.

    Args:
        vault_root: Vault root path.
        registry:   Registry dict as returned by load_registry() or modified by
                    register_capture().
    """
    path = registry_path(vault_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(registry, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# ── Registry operations ────────────────────────────────────────────────────────

def is_duplicate(content_sha256: str, registry: dict) -> bool:
    """
    Return True if the given content SHA-256 already exists in the registry.

    Args:
        content_sha256: SHA-256 hex digest of the content body.
        registry:       Registry dict from load_registry().

    Returns:
        True if a prior capture with the same content hash exists.
    """
    return content_sha256 in registry.get("entries", {})


def get_entry(content_sha256: str, registry: dict) -> dict | None:
    """
    Return the registry entry for the given SHA-256, or None if not found.

    Args:
        content_sha256: SHA-256 hex digest.
        registry:       Registry dict from load_registry().

    Returns:
        The entry dict (with keys: content_sha256, capture_id, first_captured_at,
        title, source_platform, source_url, input_class, capture_method),
        or None if no entry exists.
    """
    return registry.get("entries", {}).get(content_sha256)


def register_capture(content_sha256: str, entry: dict, registry: dict) -> None:
    """
    Add a new entry to the in-memory registry dict.

    Does NOT save to disk — call save_registry() after this.

    Only registers if the entry does not already exist.
    Existing entries are not overwritten (first-capture wins).

    Args:
        content_sha256: SHA-256 hex digest (the key).
        entry:          Entry dict with provenance fields.
        registry:       Registry dict to mutate in-place.
    """
    if "entries" not in registry:
        registry["entries"] = {}
    if content_sha256 not in registry["entries"]:
        registry["entries"][content_sha256] = entry


# ── Internal helpers ───────────────────────────────────────────────────────────

def _empty_registry() -> dict:
    return {
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "entries": {},
    }


# ── Entry builder ──────────────────────────────────────────────────────────────

def build_registry_entry(
    content_sha256: str,
    capture_id: str,
    first_captured_at: str,
    title: str,
    source_platform: str,
    source_url: str | None,
    input_class: str,
    capture_method: str,
) -> dict:
    """
    Build a canonical registry entry dict.

    All fields are stored for operator inspectability.
    Only the content_sha256 is used for dedup lookups.
    """
    return {
        "content_sha256":    content_sha256,
        "capture_id":        capture_id,
        "first_captured_at": first_captured_at,
        "title":             title,
        "source_platform":   source_platform,
        "source_url":        source_url,
        "input_class":       input_class,
        "capture_method":    capture_method,
    }
