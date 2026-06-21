"""Minimum provenance check for note promotion to canonical vault destinations."""

from __future__ import annotations

from typing import Any

# At least one of these must be present in frontmatter to satisfy provenance anchoring.
# Per provenance_migration_notes.md §7: partial retrofit is accepted;
# full modern provenance block is not required on every promoted note.
_ANCHOR_FIELDS: frozenset[str] = frozenset({"source_package_id", "promoted_from", "provenance"})


def check_promotion_minimum(
    frontmatter: dict[str, Any],
) -> tuple[bool, list[str]]:
    """
    Check that a note's frontmatter satisfies the minimum provenance requirements
    for promotion to a canonical vault destination (02_KNOWLEDGE/, 01_PROJECTS/).

    Requirements (per provenance_migration_notes.md):
      1. verification_status must be present
      2. At least one provenance anchor: source_package_id, promoted_from, or provenance

    Returns:
        (passes, errors) — passes is True only when errors is empty.
    """
    errors: list[str] = []

    if "verification_status" not in frontmatter:
        errors.append("missing required provenance field: verification_status")

    if not any(f in frontmatter for f in _ANCHOR_FIELDS):
        errors.append(
            "missing at least one provenance anchor — expected one of: "
            + ", ".join(sorted(_ANCHOR_FIELDS))
        )

    return (len(errors) == 0, errors)


def get_promotion_provenance_tier(frontmatter: dict[str, Any]) -> str:
    """
    Classify the provenance maturity of a note's frontmatter.

    Returns one of:
      "full"    — has a complete modern provenance block
      "partial" — has verification_status + at least one anchor field
      "minimal" — has verification_status only (no anchor)
      "absent"  — neither verification_status nor any anchor
    """
    has_status = "verification_status" in frontmatter
    has_anchor = any(f in frontmatter for f in _ANCHOR_FIELDS)
    has_full_provenance = "provenance" in frontmatter

    if has_full_provenance:
        return "full"
    if has_status and has_anchor:
        return "partial"
    if has_status:
        return "minimal"
    return "absent"
