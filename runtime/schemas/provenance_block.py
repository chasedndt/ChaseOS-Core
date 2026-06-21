"""Typed ProvenanceBlock and factory/mutation functions for Phase 9 provenance substrate."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .provenance_validator import (
    VALID_PROCESSING_STAGES,
    VALID_VERIFICATION_STATUSES,
    is_valid_provenance_block,
    validate_provenance_block,
)

# Ordered least → most trusted; enforces upgrade-only rule
_VERIFICATION_ORDER: list[str] = [
    "unverified",
    "operator_reviewed",
    "cross_referenced",
    "verified",
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Core type ─────────────────────────────────────────────────────────────────


@dataclass
class ProvenanceBlock:
    """
    Typed wrapper around a validated ChaseOS provenance block.

    Serialises to/from the canonical dict format understood by provenance_validator.
    CGL (Feature 12) consumes this type; trace_idea (Feature 15) traverses it.
    """

    source_ids: list[str]
    processing_stage: str
    verification_status: str
    lineage_chain: list[dict[str, Any]]
    created_at: str
    last_modified_at: str
    operator_reviewed_at: str | None
    source_refs: list[str]
    audit_refs: list[str]

    # ── Serialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_ids": list(self.source_ids),
            "processing_stage": self.processing_stage,
            "verification_status": self.verification_status,
            "lineage_chain": [dict(e) for e in self.lineage_chain],
            "created_at": self.created_at,
            "last_modified_at": self.last_modified_at,
            "operator_reviewed_at": self.operator_reviewed_at,
            "source_refs": list(self.source_refs),
            "audit_refs": list(self.audit_refs),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProvenanceBlock":
        return cls(
            source_ids=data["source_ids"],
            processing_stage=data["processing_stage"],
            verification_status=data["verification_status"],
            lineage_chain=data["lineage_chain"],
            created_at=data["created_at"],
            last_modified_at=data["last_modified_at"],
            operator_reviewed_at=data.get("operator_reviewed_at"),
            source_refs=data["source_refs"],
            audit_refs=data["audit_refs"],
        )

    # ── Validation ────────────────────────────────────────────────────────────

    def is_valid(self) -> bool:
        return is_valid_provenance_block(self.to_dict())

    def validation_errors(self) -> list[str]:
        return validate_provenance_block(self.to_dict())


# ── Factory functions ─────────────────────────────────────────────────────────


def make_minimal(
    source_ids: list[str],
    stage: str,
    ref: str,
    audit_ref: str,
    timestamp: str | None = None,
) -> ProvenanceBlock:
    """
    Build the simplest valid ProvenanceBlock from the essential anchors.

    Starts at verification_status='unverified'. Use upgrade_verification_status()
    to advance it once operator review has occurred.
    """
    if stage not in VALID_PROCESSING_STAGES:
        raise ValueError(
            f"Invalid stage '{stage}'. "
            f"Must be one of: {', '.join(sorted(VALID_PROCESSING_STAGES))}"
        )
    if not source_ids or not all(isinstance(s, str) and s.strip() for s in source_ids):
        raise ValueError("source_ids must be a non-empty list of non-empty strings")
    if not ref or not ref.strip():
        raise ValueError("ref must be a non-empty string")
    if not audit_ref or not audit_ref.strip():
        raise ValueError("audit_ref must be a non-empty string")

    ts = timestamp or _now_iso()
    return ProvenanceBlock(
        source_ids=source_ids,
        processing_stage=stage,
        verification_status="unverified",
        lineage_chain=[{"stage": stage, "ref": ref, "timestamp": ts}],
        created_at=ts,
        last_modified_at=ts,
        operator_reviewed_at=None,
        source_refs=[ref],
        audit_refs=[audit_ref],
    )


def make_from_sidecar(
    sidecar: dict[str, Any],
    content_path: str,
    audit_ref: str | None = None,
) -> ProvenanceBlock:
    """
    Derive a ProvenanceBlock from a Phase 8 sidecar dict (schema v8.x).

    Maps sidecar fields:
      capture_id       → source_ids[0]  (primary identity anchor)
      content_sha256   → source_ids[1]  (content hash anchor, prefixed sha256:)
      captured_at      → created_at / lineage timestamp
      content_path     → source_refs[0] / lineage ref
    """
    capture_id = sidecar.get("capture_id") or sidecar.get("content_sha256") or "unknown-capture"
    captured_at = sidecar.get("captured_at") or _now_iso()
    sha256 = sidecar.get("content_sha256")

    source_ids: list[str] = [capture_id]
    if sha256 and sha256 != capture_id:
        source_ids.append(f"sha256:{sha256}")

    audit_refs: list[str] = [audit_ref] if audit_ref else []

    return ProvenanceBlock(
        source_ids=source_ids,
        processing_stage="raw_capture",
        verification_status="unverified",
        lineage_chain=[
            {
                "stage": "raw_capture",
                "ref": content_path,
                "timestamp": captured_at,
            }
        ],
        created_at=captured_at,
        last_modified_at=captured_at,
        operator_reviewed_at=None,
        source_refs=[content_path],
        audit_refs=audit_refs,
    )


def make_from_source_package(
    package: dict[str, Any],
    audit_ref: str | None = None,
) -> ProvenanceBlock:
    """
    Derive a ProvenanceBlock from a SIC source package dict.

    Maps source package fields:
      id                             → source_ids[0]
      _builder_meta.content_hash_sha256 → source_ids[1] (sha256: prefix)
      origin_path                    → source_refs[0] / raw_capture lineage ref
      created_at                     → created_at / lineage timestamps
      updated_at                     → last_modified_at
    """
    package_id = package.get("id") or "unknown-package"
    origin_path = package.get("origin_path") or ""
    created_at = package.get("created_at") or _now_iso()
    updated_at = package.get("updated_at") or created_at

    builder_meta: dict[str, Any] = package.get("_builder_meta") or {}
    content_hash = builder_meta.get("content_hash_sha256")

    source_ids: list[str] = [package_id]
    if content_hash:
        source_ids.append(f"sha256:{content_hash}")

    source_refs: list[str] = [origin_path] if origin_path else []
    audit_refs: list[str] = [audit_ref] if audit_ref else []

    lineage: list[dict[str, Any]] = []
    if origin_path:
        lineage.append(
            {"stage": "raw_capture", "ref": origin_path, "timestamp": created_at}
        )
    lineage.append(
        {"stage": "source_package", "ref": package_id, "timestamp": created_at}
    )

    return ProvenanceBlock(
        source_ids=source_ids,
        processing_stage="source_package",
        verification_status="unverified",
        lineage_chain=lineage,
        created_at=created_at,
        last_modified_at=updated_at,
        operator_reviewed_at=None,
        source_refs=source_refs,
        audit_refs=audit_refs,
    )


# ── Mutation helpers (return new dicts — never mutate in place) ───────────────


def append_lineage_step(
    block: dict[str, Any],
    stage: str,
    ref: str,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """
    Return a new provenance block dict with one new lineage step appended.

    Preserves all existing lineage entries (append-only rule).
    Does not mutate the input dict.
    """
    if stage not in VALID_PROCESSING_STAGES:
        raise ValueError(
            f"Invalid stage '{stage}'. "
            f"Must be one of: {', '.join(sorted(VALID_PROCESSING_STAGES))}"
        )
    if not ref or not ref.strip():
        raise ValueError("ref must be a non-empty string")

    ts = timestamp or _now_iso()
    result = copy.deepcopy(block)
    result["lineage_chain"].append({"stage": stage, "ref": ref, "timestamp": ts})
    result["last_modified_at"] = ts
    return result


def upgrade_verification_status(
    block: dict[str, Any],
    new_status: str,
    reviewed_at: str | None = None,
) -> dict[str, Any]:
    """
    Return a new provenance block dict with verification_status upgraded.

    Rules:
      - Upgrades (unverified → operator_reviewed → cross_referenced → verified) are allowed.
      - Same-level transitions are allowed (idempotent — returns a copy unchanged).
      - Downgrades raise ValueError (provenance is append-only).
    """
    if new_status not in VALID_VERIFICATION_STATUSES:
        raise ValueError(
            f"Invalid status '{new_status}'. "
            f"Must be one of: {', '.join(sorted(VALID_VERIFICATION_STATUSES))}"
        )

    current = block.get("verification_status", "unverified")
    try:
        current_idx = _VERIFICATION_ORDER.index(current)
    except ValueError:
        current_idx = 0
    new_idx = _VERIFICATION_ORDER.index(new_status)

    if new_idx < current_idx:
        raise ValueError(
            f"Cannot downgrade verification_status from '{current}' to '{new_status}'. "
            "Provenance is append-only."
        )

    ts = reviewed_at or _now_iso()
    result = copy.deepcopy(block)
    result["verification_status"] = new_status
    result["last_modified_at"] = ts
    if new_status != "unverified":
        result["operator_reviewed_at"] = ts
    return result
