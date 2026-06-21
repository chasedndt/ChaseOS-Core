"""
router.py — ChaseOS Phase 8 Pass 2
Intake routing, naming, and slug generation for the Connector / Capture layer.

This module is the single source of truth for:
  - Which input class goes to which quarantine subfolder
  - How intake filenames are generated (deterministic, collision-resistant)
  - How title slugs are generated
  - What the physical quarantine boundary is

QUARANTINE BOUNDARY:
  All new captures write to:
    03_INPUTS/00_QUARANTINE/[class_subfolder]/

  The "00_" prefix sorts the quarantine boundary first in any directory listing.
  Legacy files from pre-Pass-2 remain in flat 03_INPUTS/[class]/ and are not
  touched by this router. They coexist safely until migrated manually.

SIC HANDOFF NOTE:
  This router knows nothing about SIC workspaces or source packages.
  Routing here is type-based intake organization only.
  SIC ingestion happens after Gate promotion — not at capture time.

Design principles:
  - Routing is type-first: the primary dimension is the content type
  - Naming encodes date + source + topic for recency sorting and identification
  - Slugs are deterministic: same title + source + date → same filename
  - No implicit date-based subdirectories: all files for a type live flat
  - Collisions are handled by appending a counter suffix

Architecture note:
  The router does not perform vault I/O. It only computes paths and names.
  Actual file writing is done by intake_writer.py.
"""

from __future__ import annotations

import re
from pathlib import Path

from .content_packet import (
    VALID_INPUT_CLASSES,
    INPUT_CLASS_TRANSCRIPT,
    INPUT_CLASS_DIGEST,
    INPUT_CLASS_NOTEBOOKLM,
    INPUT_CLASS_SOURCE,
    INPUT_CLASS_CLIPBOARD,
    INPUT_CLASS_JOURNAL,
    INPUT_CLASS_YOUTUBE_NOTE,
)


# ── Physical intake boundary ───────────────────────────────────────────────────

# Root intake directory relative to vault root
_INPUTS_DIR = Path("03_INPUTS")

# Quarantine subdirectory — the physical boundary for all new automated captures.
# "00_" sorts it first in any directory listing, making the boundary visible.
# Legacy files (pre-Pass-2) remain in flat 03_INPUTS/[class]/ and are not affected.
QUARANTINE_SUBDIR = "00_QUARANTINE"


# ── Routing table ──────────────────────────────────────────────────────────────

# Maps input_class → subfolder name under 03_INPUTS/00_QUARANTINE/
#
# Rationale for type-first organization:
#   - Type grouping keeps each class of content cleanly separated
#   - Flat per-type layout (no year/month subdirs) keeps navigation simple
#     for vault sizes we expect (<100 files per type)
#   - Date in filename enables recency sorting without date-based folders
#   - Source in filename enables quick identification without opening the file
#   - Quarantine boundary (00_QUARANTINE/) makes the intake zone physically distinct

INPUT_CLASS_TO_SUBFOLDER: dict[str, str] = {
    INPUT_CLASS_TRANSCRIPT:   "Transcript-Raw",
    INPUT_CLASS_DIGEST:       "Digests",
    INPUT_CLASS_NOTEBOOKLM:   "NotebookLM",
    INPUT_CLASS_SOURCE:       "Sources",
    INPUT_CLASS_CLIPBOARD:    "Clipboard",
    INPUT_CLASS_JOURNAL:      "Journal-Raw",
    INPUT_CLASS_YOUTUBE_NOTE: "YouTube-Notes",
}

# Inverse: subfolder → input_class
SUBFOLDER_TO_INPUT_CLASS: dict[str, str] = {
    v: k for k, v in INPUT_CLASS_TO_SUBFOLDER.items()
}


# ── Routing functions ──────────────────────────────────────────────────────────

def route_input_class(input_class: str, vault_root: Path) -> Path:
    """
    Return the absolute path to the quarantine subfolder for input_class.

    Path pattern: <vault_root>/03_INPUTS/00_QUARANTINE/<subfolder>/

    Raises ValueError if input_class is not recognized.
    """
    subfolder = INPUT_CLASS_TO_SUBFOLDER.get(input_class)
    if subfolder is None:
        raise ValueError(
            f"Unknown input_class '{input_class}'. "
            f"Valid classes: {sorted(VALID_INPUT_CLASSES)}"
        )
    return vault_root / _INPUTS_DIR / QUARANTINE_SUBDIR / subfolder


def get_route_reason(input_class: str) -> str:
    """
    Return a human-readable string explaining the routing decision.

    Used in sidecar metadata for operator transparency.

    Example:
        "input_class='transcript' -> 03_INPUTS/00_QUARANTINE/Transcript-Raw/"
    """
    subfolder = INPUT_CLASS_TO_SUBFOLDER.get(input_class, "unknown")
    return (
        f"input_class='{input_class}' -> "
        f"{_INPUTS_DIR}/{QUARANTINE_SUBDIR}/{subfolder}/"
    )


# ── Slug generation ───────────────────────────────────────────────────────────

def make_title_slug(title: str, max_len: int = 50) -> str:
    """
    Generate a URL-safe, deterministic slug from a title string.

    Algorithm:
    1. Lowercase
    2. Replace all non-alphanumeric characters with hyphens
    3. Collapse consecutive hyphens into one
    4. Strip leading/trailing hyphens
    5. Truncate to max_len characters
    6. Strip any trailing hyphen introduced by truncation

    Examples:
        "Market Microstructure Lecture" -> "market-microstructure-lecture"
        "Crypto Perps: Funding Rates Q1 2026" -> "crypto-perps-funding-rates-q1-2026"
        "  Multi-Agent  Tool Use Patterns  " -> "multi-agent-tool-use-patterns"
    """
    slug = title.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug)
    slug = slug.strip("-")
    slug = slug[:max_len].rstrip("-")
    if not slug:
        slug = "untitled"
    return slug


def make_source_slug(source_platform: str) -> str:
    """
    Normalize a source_platform identifier into a slug-safe string.

    Examples: "YouTube" -> "youtube", "Perplexity AI" -> "perplexity-ai"
    """
    slug = source_platform.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug or "unknown"


def make_filename(
    input_class: str,
    source_platform: str,
    title: str,
    captured_at: str,
) -> str:
    """
    Generate a deterministic filename for an intake file.

    Canonical Phase 8 naming pattern:
        YYYYMMDD-HHMMSS__[class]__[source]__[slug].md

    Examples:
        "20260327-143022__transcript__youtube__order-flow-market-microstructure.md"
        "20260327-143022__digest__perplexity__crypto-perps-funding-rates.md"
        "20260327-143022__notebooklm__notebooklm__multi-agent-tool-use-patterns.md"
        "20260327-143022__source__web__defi-lending-mechanics.md"

    Encoding:
        - Timestamp (YYYYMMDD-HHMMSS): collision-safe; sortable by capture time
        - Class (__[class]__): intake class for programmatic parsing
        - Source (__[source]__): where it came from; quick identification
        - Slug (__[slug]): human-readable topic identifier

    Double-underscore (__) separates fields for programmatic parsing:
        filename.split("__") -> [timestamp, class, source, slug_noext]

    Args:
        input_class:     Intake class string (e.g. "transcript", "digest").
        source_platform: Source identifier (e.g. "youtube", "perplexity").
        title:           Human-readable title to slugify.
        captured_at:     ISO 8601 UTC timestamp from the ContentPacket.

    Returns:
        A complete .md filename string (no directory component).
    """
    # Compact timestamp: YYYYMMDD-HHMMSS
    ts = captured_at.replace("-", "").replace(":", "").replace("T", "-")[:15]
    # ts is now like "20260327-143022" from "2026-03-27T14:30:22..."

    class_slug  = make_source_slug(input_class)    # lowercase, hyphenated
    source_slug = make_source_slug(source_platform)
    title_slug  = make_title_slug(title, max_len=45)

    return f"{ts}__{class_slug}__{source_slug}__{title_slug}.md"


def resolve_unique_path(target_dir: Path, filename: str) -> Path:
    """
    Return a path that does not already exist by appending _N suffix if needed.

    Args:
        target_dir: The directory where the file will be written.
        filename:   The desired filename.

    Returns:
        A Path that does not yet exist (either the original or with _2, _3, ... suffix).
    """
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    candidate = target_dir / filename
    if not candidate.exists():
        return candidate

    counter = 2
    while True:
        new_name = f"{stem}_{counter}{suffix}"
        candidate = target_dir / new_name
        if not candidate.exists():
            return candidate
        counter += 1
        if counter > 9999:
            raise RuntimeError(
                f"Could not find a unique filename for '{filename}' in {target_dir}. "
                "Something is wrong."
            )
