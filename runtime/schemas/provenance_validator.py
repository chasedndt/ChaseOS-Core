"""Validation helpers for Phase 9 provenance blocks."""

from __future__ import annotations

from datetime import datetime
from typing import Any

REQUIRED_FIELDS = {
    "source_ids",
    "processing_stage",
    "verification_status",
    "lineage_chain",
    "created_at",
    "last_modified_at",
    "operator_reviewed_at",
    "source_refs",
    "audit_refs",
}

VALID_PROCESSING_STAGES = {
    "raw_capture",
    "quarantine",
    "normalized",
    "source_package",
    "briefing_input",
    "generated",
    "reviewed",
    "promoted",
    "canonical",
}

VALID_VERIFICATION_STATUSES = {
    "unverified",
    "operator_reviewed",
    "cross_referenced",
    "verified",
}


def _is_iso8601(value: str) -> bool:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def validate_provenance_block(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    missing = sorted(REQUIRED_FIELDS - data.keys())
    for field in missing:
        errors.append(f"missing required field: {field}")

    if missing:
        return errors

    source_ids = data.get("source_ids")
    if not isinstance(source_ids, list) or not source_ids or not all(isinstance(x, str) and x.strip() for x in source_ids):
        errors.append("source_ids must be a non-empty list of strings")

    processing_stage = data.get("processing_stage")
    if processing_stage not in VALID_PROCESSING_STAGES:
        errors.append(
            "processing_stage must be one of: " + ", ".join(sorted(VALID_PROCESSING_STAGES))
        )

    verification_status = data.get("verification_status")
    if verification_status not in VALID_VERIFICATION_STATUSES:
        errors.append(
            "verification_status must be one of: " + ", ".join(sorted(VALID_VERIFICATION_STATUSES))
        )

    for field in ("created_at", "last_modified_at"):
        value = data.get(field)
        if not isinstance(value, str) or not _is_iso8601(value):
            errors.append(f"{field} must be an ISO-8601 timestamp string")

    reviewed_at = data.get("operator_reviewed_at")
    if reviewed_at is not None and (not isinstance(reviewed_at, str) or not _is_iso8601(reviewed_at)):
        errors.append("operator_reviewed_at must be null or an ISO-8601 timestamp string")

    for field in ("source_refs", "audit_refs"):
        value = data.get(field)
        if not isinstance(value, list) or not all(isinstance(x, str) and x.strip() for x in value):
            errors.append(f"{field} must be a list of non-empty strings")

    lineage_chain = data.get("lineage_chain")
    if not isinstance(lineage_chain, list) or not lineage_chain:
        errors.append("lineage_chain must be a non-empty list")
    else:
        for idx, entry in enumerate(lineage_chain):
            if not isinstance(entry, dict):
                errors.append(f"lineage_chain[{idx}] must be an object")
                continue
            for field in ("stage", "ref", "timestamp"):
                if field not in entry:
                    errors.append(f"lineage_chain[{idx}] missing required field: {field}")
            stage = entry.get("stage")
            if stage not in VALID_PROCESSING_STAGES:
                errors.append(
                    f"lineage_chain[{idx}].stage must be one of: " + ", ".join(sorted(VALID_PROCESSING_STAGES))
                )
            ref = entry.get("ref")
            if not isinstance(ref, str) or not ref.strip():
                errors.append(f"lineage_chain[{idx}].ref must be a non-empty string")
            timestamp = entry.get("timestamp")
            if not isinstance(timestamp, str) or not _is_iso8601(timestamp):
                errors.append(f"lineage_chain[{idx}].timestamp must be an ISO-8601 timestamp string")

    return errors


def is_valid_provenance_block(data: dict[str, Any]) -> bool:
    return not validate_provenance_block(data)
