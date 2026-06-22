"""
context_governance.py — ChaseOS Phase 9 Feature 12
Context Governance Layer (CGL)

Adds per-note governance metadata that AOR, SIC, and the Gate consult before
using a note as input to any action.

CGL does not change note content — it annotates notes with governance metadata
that automated systems can consult at runtime.

CGL frontmatter fields (added to note YAML frontmatter):
    trust_level:     untrusted | reviewed | verified | canonical
    sensitivity:     internal | operator-only | shareable
    promotion_stage: quarantine | promoted | synthesized | canonical
    allowed_surfaces: list[str]  — empty means no surface restriction

Action types resolved by CGL:
    read_context         — reading note content as LLM context / reasoning input
    read_metadata        — reading note metadata / frontmatter only
    write_canonical      — writing to canonical vault (02_KNOWLEDGE/, 01_PROJECTS/)
    write_log            — writing to logs (07_LOGS/)
    surface_external     — surfacing content to external services (Discord, email)
    index_for_retrieval  — indexing for SIC retrieval

Eligibility results:
    eligible    — note can be used in this action
    restricted  — note can be used with restrictions (metadata only, not content body)
    blocked     — note cannot be used in this action; violation is logged

Public API:
    read_note_cgl(path)                           -> CglMetadata
    resolve_context_eligibility(cgl, action, card) -> CglResult
    check_write_compatibility(cgl, path, card)     -> CglResult
    resolve_notes_eligibility(paths, action, card, vault_root) -> (bool, list[CglResult])
    log_cgl_violation(violation, vault_root)       -> None
    cgl_trust_level_from_sic(user_trust_level)     -> str
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:
    import yaml as _yaml
    _YAML_AVAILABLE = True
except ImportError:  # pragma: no cover
    _YAML_AVAILABLE = False

# ── Schema constants ──────────────────────────────────────────────────────────

TRUST_LEVELS: list[str] = ["untrusted", "reviewed", "verified", "canonical"]
SENSITIVITY_LEVELS: set[str] = {"internal", "operator-only", "shareable"}
PROMOTION_STAGES: list[str] = ["quarantine", "promoted", "synthesized", "canonical"]
ACTION_TYPES: set[str] = {
    "read_context",
    "read_metadata",
    "write_canonical",
    "write_log",
    "surface_external",
    "index_for_retrieval",
}

# Mapping from SIC user_trust_level → CGL trust_level
_SIC_TO_CGL_TRUST: dict[str, str] = {
    "trusted":   "verified",
    "reviewed":  "reviewed",
    "untrusted": "untrusted",
}

# Default CGL metadata by vault path prefix (for notes without CGL frontmatter)
# Ordered: more specific prefixes first.
_DEFAULT_BY_PREFIX: list[tuple[str, dict[str, Any]]] = [
    ("03_INPUTS/00_QUARANTINE", {
        "trust_level": "untrusted",
        "sensitivity": "internal",
        "promotion_stage": "quarantine",
        "allowed_surfaces": [],
    }),
    ("03_INPUTS", {
        "trust_level": "untrusted",
        "sensitivity": "internal",
        "promotion_stage": "quarantine",
        "allowed_surfaces": [],
    }),
    ("02_KNOWLEDGE", {
        "trust_level": "reviewed",
        "sensitivity": "internal",
        "promotion_stage": "promoted",
        "allowed_surfaces": [],
    }),
    ("01_PROJECTS", {
        "trust_level": "reviewed",
        "sensitivity": "internal",
        "promotion_stage": "promoted",
        "allowed_surfaces": [],
    }),
    ("07_LOGS", {
        "trust_level": "reviewed",
        "sensitivity": "internal",
        "promotion_stage": "canonical",
        "allowed_surfaces": [],
    }),
    ("00_HOME", {
        "trust_level": "canonical",
        "sensitivity": "operator-only",
        "promotion_stage": "canonical",
        "allowed_surfaces": [],
    }),
    ("runtime", {
        "trust_level": "verified",
        "sensitivity": "internal",
        "promotion_stage": "canonical",
        "allowed_surfaces": [],
    }),
]

_GLOBAL_DEFAULT: dict[str, Any] = {
    "trust_level": "reviewed",
    "sensitivity": "internal",
    "promotion_stage": "promoted",
    "allowed_surfaces": [],
}


# ── Data types ────────────────────────────────────────────────────────────────


@dataclass
class CglMetadata:
    """
    Governance metadata for a single vault note or artifact.

    Derived from note YAML frontmatter when present; otherwise from
    path-based defaults (fail-open — missing CGL never blocks existing notes).
    """
    trust_level: str = "reviewed"          # untrusted|reviewed|verified|canonical
    sensitivity: str = "internal"          # internal|operator-only|shareable
    promotion_stage: str = "promoted"      # quarantine|promoted|synthesized|canonical
    allowed_surfaces: list[str] = field(default_factory=list)
    source: str = "default"                # "frontmatter" | "path-default" | "global-default"

    def trust_index(self) -> int:
        try:
            return TRUST_LEVELS.index(self.trust_level)
        except ValueError:
            return 0


@dataclass
class CglResult:
    """Resolved eligibility for a single (note, action) pair."""
    eligibility: str          # "eligible" | "restricted" | "blocked"
    reason: str
    note_ref: str             # vault-relative path or identifier
    action_type: str
    cgl_metadata: Optional[CglMetadata] = None


@dataclass
class CglViolation:
    """Record of a CGL access violation (blocked or restricted result)."""
    note_ref: str
    action_type: str
    eligibility: str          # "blocked" | "restricted"
    reason: str
    role_card_id: str
    timestamp_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    violation_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])


# ── Frontmatter parsing ───────────────────────────────────────────────────────


def _parse_frontmatter(path: Path) -> dict[str, Any]:
    """
    Extract YAML frontmatter from a markdown file.

    Returns an empty dict if:
      - the file does not exist
      - the file has no frontmatter delimiters
      - the frontmatter is not valid YAML

    Never raises.
    """
    if not path.exists() or not path.is_file():
        return {}

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}

    # Frontmatter must start at line 1 with ---
    if not text.startswith("---"):
        return {}

    # Find closing ---
    end = text.find("\n---", 3)
    if end == -1:
        return {}

    fm_text = text[3:end].strip()
    if not fm_text:
        return {}

    if _YAML_AVAILABLE:
        try:
            parsed = _yaml.safe_load(fm_text)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:  # noqa: BLE001
            return {}
    else:
        # Minimal fallback: key: value line parser (no nested structures)
        result: dict[str, Any] = {}
        for line in fm_text.splitlines():
            m = re.match(r"^(\w[\w_-]*):\s*(.*)$", line.strip())
            if m:
                result[m.group(1)] = m.group(2).strip()
        return result


def _default_cgl_for_path(path: Path, vault_root: Path) -> dict[str, Any]:
    """Return the path-based default CGL values for a given note path."""
    try:
        relative = path.relative_to(vault_root)
        relative_str = str(relative).replace("\\", "/")
    except ValueError:
        return _GLOBAL_DEFAULT

    for prefix, defaults in _DEFAULT_BY_PREFIX:
        if relative_str.startswith(prefix):
            return defaults

    return _GLOBAL_DEFAULT


# ── Public API ────────────────────────────────────────────────────────────────


def read_note_cgl(path: Path, vault_root: Optional[Path] = None) -> CglMetadata:
    """
    Read CGL governance metadata from a note's YAML frontmatter.

    If the note has no CGL frontmatter fields, path-based defaults are applied.
    Never raises — fail-open: a note without CGL metadata is eligible by default.
    """
    fm = _parse_frontmatter(path)

    cgl_fields = {"trust_level", "sensitivity", "promotion_stage", "allowed_surfaces"}
    has_cgl_fm = bool(cgl_fields & fm.keys())

    if has_cgl_fm:
        trust_level = fm.get("trust_level", "reviewed")
        sensitivity = fm.get("sensitivity", "internal")
        promotion_stage = fm.get("promotion_stage", "promoted")
        allowed_surfaces = fm.get("allowed_surfaces") or []
        if isinstance(allowed_surfaces, str):
            allowed_surfaces = [allowed_surfaces]

        # Validate and clamp to known values (graceful)
        if trust_level not in TRUST_LEVELS:
            trust_level = "reviewed"
        if sensitivity not in SENSITIVITY_LEVELS:
            sensitivity = "internal"
        if promotion_stage not in PROMOTION_STAGES:
            promotion_stage = "promoted"

        return CglMetadata(
            trust_level=trust_level,
            sensitivity=sensitivity,
            promotion_stage=promotion_stage,
            allowed_surfaces=list(allowed_surfaces),
            source="frontmatter",
        )

    # No CGL frontmatter — apply path-based defaults
    if vault_root is not None:
        defaults = _default_cgl_for_path(path, vault_root)
        return CglMetadata(
            trust_level=defaults["trust_level"],
            sensitivity=defaults["sensitivity"],
            promotion_stage=defaults["promotion_stage"],
            allowed_surfaces=list(defaults.get("allowed_surfaces", [])),
            source="path-default",
        )

    return CglMetadata(source="global-default")


def resolve_context_eligibility(
    cgl: CglMetadata,
    action_type: str,
    role_card: dict[str, Any],
    note_ref: str = "",
) -> CglResult:
    """
    Resolve whether a note with the given CGL metadata is eligible for action_type
    under the given role card.

    Resolution rules (in priority order):
      1. BLOCKED: operator-only content → surface_external action
      2. BLOCKED: untrusted content → write_canonical or surface_external
      3. BLOCKED: quarantine-stage content → write_canonical or surface_external
      4. BLOCKED: allowed_surfaces is set and role card action not in allowed_surfaces
      5. BLOCKED: role card declares cgl_min_trust_level and note is below it
      6. RESTRICTED: untrusted content → read_context
      7. RESTRICTED: quarantine content → read_context or index_for_retrieval
      8. Otherwise: ELIGIBLE
    """
    # ── Block rules ───────────────────────────────────────────────────────────

    if cgl.sensitivity == "operator-only" and action_type == "surface_external":
        return CglResult(
            eligibility="blocked",
            reason="operator-only content cannot be surfaced to external systems",
            note_ref=note_ref,
            action_type=action_type,
            cgl_metadata=cgl,
        )

    if cgl.trust_level == "untrusted" and action_type in ("write_canonical", "surface_external"):
        return CglResult(
            eligibility="blocked",
            reason=(
                f"untrusted content cannot be used in '{action_type}' — "
                "operator review required before this action"
            ),
            note_ref=note_ref,
            action_type=action_type,
            cgl_metadata=cgl,
        )

    if cgl.promotion_stage == "quarantine" and action_type in ("write_canonical", "surface_external"):
        return CglResult(
            eligibility="blocked",
            reason=(
                f"quarantine-stage content cannot be used in '{action_type}' — "
                "content must be promoted before this action"
            ),
            note_ref=note_ref,
            action_type=action_type,
            cgl_metadata=cgl,
        )

    # Role card minimum trust level check
    card_min_trust = role_card.get("cgl_min_trust_level", "")
    if card_min_trust and card_min_trust in TRUST_LEVELS:
        min_idx = TRUST_LEVELS.index(card_min_trust)
        if cgl.trust_index() < min_idx:
            return CglResult(
                eligibility="blocked",
                reason=(
                    f"role card requires cgl_min_trust_level='{card_min_trust}' "
                    f"but note has trust_level='{cgl.trust_level}'"
                ),
                note_ref=note_ref,
                action_type=action_type,
                cgl_metadata=cgl,
            )

    # Allowed surfaces check
    if cgl.allowed_surfaces:
        card_id = role_card.get("id", "")
        if card_id and card_id not in cgl.allowed_surfaces:
            return CglResult(
                eligibility="blocked",
                reason=(
                    f"note has allowed_surfaces={cgl.allowed_surfaces!r} — "
                    f"role card '{card_id}' is not in the allowed list"
                ),
                note_ref=note_ref,
                action_type=action_type,
                cgl_metadata=cgl,
            )

    # ── Restrict rules ────────────────────────────────────────────────────────

    if cgl.trust_level == "untrusted" and action_type == "read_context":
        return CglResult(
            eligibility="restricted",
            reason=(
                "untrusted content must be treated as data only — "
                "do not use as instruction context; read metadata only"
            ),
            note_ref=note_ref,
            action_type=action_type,
            cgl_metadata=cgl,
        )

    if cgl.promotion_stage == "quarantine" and action_type in ("read_context", "index_for_retrieval"):
        return CglResult(
            eligibility="restricted",
            reason=(
                f"quarantine-stage content may only be accessed as reference — "
                f"'{action_type}' is allowed with restrictions; content body should not be treated as fact"
            ),
            note_ref=note_ref,
            action_type=action_type,
            cgl_metadata=cgl,
        )

    # ── Eligible ──────────────────────────────────────────────────────────────

    return CglResult(
        eligibility="eligible",
        reason="CGL check passed",
        note_ref=note_ref,
        action_type=action_type,
        cgl_metadata=cgl,
    )


def check_write_compatibility(
    note_cgl: CglMetadata,
    writeback_path: str,
    role_card: dict[str, Any],
    note_ref: str = "",
) -> CglResult:
    """
    Gate-adjacent check: determine whether a writeback to writeback_path is
    compatible with the CGL metadata of the source note being used as input.

    A canonical writeback (02_KNOWLEDGE/, 01_PROJECTS/) sourced from untrusted
    or quarantine content is blocked.
    """
    canonical_prefixes = ("02_KNOWLEDGE/", "01_PROJECTS/")
    is_canonical_write = any(
        writeback_path.startswith(p) for p in canonical_prefixes
    )

    if is_canonical_write:
        return resolve_context_eligibility(
            cgl=note_cgl,
            action_type="write_canonical",
            role_card=role_card,
            note_ref=note_ref,
        )

    # Log writes are less restrictive
    return resolve_context_eligibility(
        cgl=note_cgl,
        action_type="write_log",
        role_card=role_card,
        note_ref=note_ref,
    )


def resolve_notes_eligibility(
    note_paths: list[str],
    action_type: str,
    role_card: dict[str, Any],
    vault_root: Path,
) -> tuple[bool, list[CglResult]]:
    """
    Batch CGL check for a list of vault-relative note paths.

    Returns (all_eligible: bool, results: list[CglResult]).
    all_eligible is True only if every result is "eligible".
    Paths that are directories (not individual notes) are skipped.
    """
    results: list[CglResult] = []
    all_eligible = True

    for raw_path in note_paths:
        full_path = vault_root / raw_path.replace("\\", "/")
        if not full_path.exists() or full_path.is_dir():
            continue  # skip directories and missing paths

        cgl = read_note_cgl(full_path, vault_root=vault_root)
        result = resolve_context_eligibility(
            cgl=cgl,
            action_type=action_type,
            role_card=role_card,
            note_ref=raw_path,
        )
        results.append(result)
        if result.eligibility != "eligible":
            all_eligible = False

    return all_eligible, results


def log_cgl_violation(violation: CglViolation, vault_root: Path) -> None:
    """
    Append a CGL violation event to 07_LOGS/Agent-Activity/cgl-violations.jsonl.

    Uses JSONL (newline-delimited JSON) so violation records are individually
    parseable and the file can be appended to without read-modify-write.

    Never raises — CGL logging failure must not block the primary execution path.
    """
    try:
        violation_dir = vault_root / "07_LOGS" / "Agent-Activity"
        violation_dir.mkdir(parents=True, exist_ok=True)
        violation_file = violation_dir / "cgl-violations.jsonl"

        record = {
            "violation_id": violation.violation_id,
            "timestamp_utc": violation.timestamp_utc,
            "note_ref": violation.note_ref,
            "action_type": violation.action_type,
            "eligibility": violation.eligibility,
            "reason": violation.reason,
            "role_card_id": violation.role_card_id,
        }

        with violation_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    except Exception:  # noqa: BLE001
        pass  # Violation logging is best-effort; never propagate


def cgl_trust_level_from_sic(user_trust_level: Optional[str]) -> str:
    """
    Map a SIC source package user_trust_level to a CGL trust_level string.

    Used when enriching SIC evidence packets with CGL tier information.
    """
    return _SIC_TO_CGL_TRUST.get(user_trust_level or "", "untrusted")
