"""
runtime/memory/incident_review.py — operator tooling for the incident backlog.

The behavior tripwire (detective layer) raises a MEDIUM alert when unreviewed
incident candidates accumulate in runtime/memory/repair/{runtime}.json. The
writer (growth.record_incident_candidate) only ever appends unreviewed
candidates — historically nothing marked them reviewed, so the backlog could
only grow and the alert could never clear.

This module is the missing operator surface:
  - incident_stats   — backlog counts, grouped by reason signature
  - list_incidents   — enumerate candidates (optionally filtered)
  - dedup_incidents  — collapse identical candidates into one (+ duplicate_count)
  - review_incidents — mark matching candidates operator_reviewed=true with a
                       resolution note + reviewed_at (this is what clears alerts)

It is operator-authority only. It never bypasses the Gate, grants permissions,
or auto-heals — it records operator review decisions. Within the repair-memory
governance boundary ("may inform operator review … cannot bypass Gate policy or
apply self-repair without explicit authority").
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from runtime.memory.growth import (
    _load_json_or_default,
    _now_iso,
    _repair_default,
    _repair_path,
    _write_json,
)


def reason_signature(reason: str) -> str:
    """Collapse an escalation_reason to a stable group key for stats/dedup."""
    r = reason or ""
    if "ANTHROPIC_API_KEY" in r:
        return "anthropic_api_key_config_gap"
    low = r.lower()
    if "api key" in low or "api_key" in low:
        return "other_api_key_config_gap"
    if "not found in registr" in low:
        return "workflow_not_in_registry"
    if "not in the task type table" in low:
        return "task_type_not_in_table"
    return (r[:60] or "unknown").strip()


def _candidate_matches(
    cand: dict,
    *,
    reason_contains: Optional[str],
    workflow_id: Optional[str],
    signature: Optional[str],
    only_unreviewed: bool,
) -> bool:
    if only_unreviewed and cand.get("operator_reviewed"):
        return False
    if workflow_id and cand.get("workflow_id") != workflow_id:
        return False
    if reason_contains and reason_contains.lower() not in str(cand.get("escalation_reason", "")).lower():
        return False
    if signature and reason_signature(str(cand.get("escalation_reason", ""))) != signature:
        return False
    return True


def incident_stats(runtime_id: str, vault_root: Path) -> dict:
    """Return backlog counts for a runtime, grouped by reason signature."""
    path = _repair_path(runtime_id, vault_root)
    repair = _load_json_or_default(path, _repair_default(runtime_id))
    candidates = list(repair.get("incident_candidates") or [])
    total = len(candidates)
    reviewed = sum(1 for c in candidates if c.get("operator_reviewed"))
    by_sig: dict[str, dict[str, int]] = {}
    for c in candidates:
        sig = reason_signature(str(c.get("escalation_reason", "")))
        bucket = by_sig.setdefault(sig, {"total": 0, "unreviewed": 0})
        bucket["total"] += 1
        if not c.get("operator_reviewed"):
            bucket["unreviewed"] += 1
    return {
        "runtime_id": runtime_id,
        "total": total,
        "reviewed": reviewed,
        "unreviewed": total - reviewed,
        "by_signature": by_sig,
    }


def list_incidents(
    runtime_id: str,
    vault_root: Path,
    *,
    reason_contains: Optional[str] = None,
    workflow_id: Optional[str] = None,
    signature: Optional[str] = None,
    only_unreviewed: bool = False,
) -> list[dict]:
    path = _repair_path(runtime_id, vault_root)
    repair = _load_json_or_default(path, _repair_default(runtime_id))
    return [
        c
        for c in (repair.get("incident_candidates") or [])
        if _candidate_matches(
            c,
            reason_contains=reason_contains,
            workflow_id=workflow_id,
            signature=signature,
            only_unreviewed=only_unreviewed,
        )
    ]


def dedup_incidents(runtime_id: str, vault_root: Path, *, dry_run: bool = True) -> dict:
    """
    Collapse identical candidates into a single representative entry.

    Identity = (workflow_id, outcome, escalation_reason, operator_reviewed). The
    earliest entry is kept and annotated with duplicate_count, first_recorded_at,
    and last_recorded_at. Reviewed and unreviewed entries are never merged.
    """
    path = _repair_path(runtime_id, vault_root)
    repair = _load_json_or_default(path, _repair_default(runtime_id))
    candidates = list(repair.get("incident_candidates") or [])
    before = len(candidates)

    merged: dict[tuple, dict] = {}
    order: list[tuple] = []
    for c in candidates:
        key = (
            c.get("workflow_id"),
            c.get("outcome"),
            c.get("escalation_reason", ""),
            bool(c.get("operator_reviewed")),
        )
        ts = c.get("recorded_at", "")
        if key not in merged:
            merged[key] = dict(c)
            merged[key]["duplicate_count"] = 1
            merged[key]["first_recorded_at"] = ts
            merged[key]["last_recorded_at"] = ts
            order.append(key)
        else:
            rep = merged[key]
            rep["duplicate_count"] += 1
            if ts and (not rep.get("first_recorded_at") or ts < rep["first_recorded_at"]):
                rep["first_recorded_at"] = ts
            if ts and (not rep.get("last_recorded_at") or ts > rep["last_recorded_at"]):
                rep["last_recorded_at"] = ts

    deduped = [merged[k] for k in order]
    after = len(deduped)

    if not dry_run and after != before:
        repair["incident_candidates"] = deduped
        repair["status"] = "active"
        repair["updated_at"] = _now_iso()
        _write_json(path, repair)

    return {
        "runtime_id": runtime_id,
        "dry_run": dry_run,
        "before": before,
        "after": after,
        "collapsed": before - after,
    }


def review_incidents(
    runtime_id: str,
    vault_root: Path,
    *,
    reason_contains: Optional[str] = None,
    workflow_id: Optional[str] = None,
    signature: Optional[str] = None,
    note: str = "",
    reviewed_by: str = "operator",
    dry_run: bool = True,
) -> dict:
    """
    Mark matching candidates operator_reviewed=true with a resolution note.

    This is what clears the tripwire MEDIUM alert. At least one filter must be
    supplied (reason_contains / workflow_id / signature) so a bare call cannot
    blanket-clear the whole backlog by accident.
    """
    if not any([reason_contains, workflow_id, signature]):
        raise ValueError(
            "review_incidents requires a filter (reason_contains, workflow_id, or signature) "
            "to avoid clearing the entire backlog unintentionally"
        )

    path = _repair_path(runtime_id, vault_root)
    repair = _load_json_or_default(path, _repair_default(runtime_id))
    candidates = list(repair.get("incident_candidates") or [])

    matched = 0
    marked = 0
    stamp = _now_iso()
    for c in candidates:
        if not _candidate_matches(
            c,
            reason_contains=reason_contains,
            workflow_id=workflow_id,
            signature=signature,
            only_unreviewed=False,
        ):
            continue
        matched += 1
        if c.get("operator_reviewed"):
            continue
        marked += 1
        if not dry_run:
            c["operator_reviewed"] = True
            c["reviewed_at"] = stamp
            c["reviewed_by"] = reviewed_by
            if note:
                c["resolution_note"] = note

    if not dry_run and marked:
        repair["incident_candidates"] = candidates
        repair["status"] = "active"
        repair["updated_at"] = stamp
        _write_json(path, repair)

    return {
        "runtime_id": runtime_id,
        "dry_run": dry_run,
        "matched": matched,
        "marked_reviewed": marked,
        "note": note,
        "reviewed_by": reviewed_by,
    }
