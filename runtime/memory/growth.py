"""
growth.py — Governed Runtime Memory Growth

Write-side memory growth for Layer C runtime memory surfaces.
Called by the AOR engine after each workflow execution to accumulate
behavioural data into nav-map and repair-memory substrates.

Governed constraints:
  - All writes are fail-open (never abort the calling execution path)
  - Memory growth cannot expand permissions, bypass Gate, or override doctrine
  - Nav-map successful_route_patterns capped at _ROUTE_CAP entries (oldest dropped)
  - Nav-map common_escalation_triggers capped at _TRIGGER_CAP entries (oldest dropped)
  - Repair memory repair_patterns capped at _REPAIR_CAP entries (oldest dropped)
  - Memory is advisory only; reads in inspector.py confirm what was written

Public API:
    warm_nav_map_from_execution(runtime_id, vault_root, audit_record) -> None
    record_repair_pattern(runtime_id, vault_root, *, workflow_id, failure_context,
                          repair_action, resolved) -> None
    export_memory_snapshot(runtime_id, vault_root) -> dict
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


_ROUTE_CAP = 50
_TRIGGER_CAP = 20
_REPAIR_CAP = 100


# ── Paths ─────────────────────────────────────────────────────────────────────

def _nav_map_path(runtime_id: str, vault_root: Path) -> Path:
    return vault_root / "runtime" / "memory" / "nav" / runtime_id / "nav-map.json"


def _repair_path(runtime_id: str, vault_root: Path) -> Path:
    return vault_root / "runtime" / "memory" / "repair" / f"{runtime_id}.json"


def _profile_path(runtime_id: str, vault_root: Path) -> Path:
    return vault_root / "runtime" / "memory" / "adapters" / runtime_id / "profile.json"


def _scorecard_path(runtime_id: str, vault_root: Path) -> Path:
    return vault_root / "runtime" / "memory" / "scorecards" / f"{runtime_id}.json"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json_or_default(path: Path, default: dict) -> dict:
    try:
        if not path.exists():
            return dict(default)
        text = path.read_text(encoding="utf-8", errors="replace")
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            return dict(default)
        return parsed
    except Exception:  # noqa: BLE001
        return dict(default)


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


# ── Nav-map growth ────────────────────────────────────────────────────────────

def _nav_map_default(runtime_id: str) -> dict:
    return {
        "runtime_id": runtime_id,
        "version": "0.1",
        "status": "seeded",
        "updated": _now_iso()[:10],
        "preferred_read_routes": [],
        "trusted_zones": [],
        "risk_zones": [],
        "successful_route_patterns": [],
        "common_escalation_triggers": [],
        "governance_boundary": (
            "Navigation map is advisory. Preferred routes do not grant read permissions "
            "beyond what the role card write_scope and Gate policy allow."
        ),
    }


def _extract_required_reads(audit_record: dict) -> list[str]:
    """Extract required_reads from an AOR audit record's manifest_snapshot."""
    manifest = audit_record.get("manifest_snapshot") or {}
    reads = manifest.get("required_reads") or []
    return [str(r) for r in reads if r]


def warm_nav_map_from_execution(
    runtime_id: str,
    vault_root: Path,
    audit_record: dict[str, Any],
) -> None:
    """
    Update the nav-map for runtime_id from a completed AOR execution.

    On success: appends required_reads as a successful_route_pattern.
    On permission_ceiling escalation: appends the trigger to common_escalation_triggers.

    Fail-open — never raises; never affects the calling execution path.
    """
    try:
        path = _nav_map_path(runtime_id, vault_root)
        nav = _load_json_or_default(path, _nav_map_default(runtime_id))

        status = audit_record.get("status") or ""
        stage_reached = audit_record.get("stage_reached") or ""
        workflow_id = audit_record.get("workflow_id") or ""
        escalation_reason = audit_record.get("escalation_reason") or ""

        if status == "success":
            reads = _extract_required_reads(audit_record)
            if reads:
                patterns: list = list(nav.get("successful_route_patterns") or [])
                entry = {
                    "workflow_id": workflow_id,
                    "reads": reads,
                    "recorded_at": _now_iso(),
                }
                patterns.append(entry)
                if len(patterns) > _ROUTE_CAP:
                    patterns = patterns[-_ROUTE_CAP:]
                nav["successful_route_patterns"] = patterns

        if stage_reached == "permission_ceiling" and escalation_reason:
            triggers: list = list(nav.get("common_escalation_triggers") or [])
            trigger_entry = {
                "workflow_id": workflow_id,
                "reason": escalation_reason,
                "recorded_at": _now_iso(),
            }
            triggers.append(trigger_entry)
            if len(triggers) > _TRIGGER_CAP:
                triggers = triggers[-_TRIGGER_CAP:]
            nav["common_escalation_triggers"] = triggers

        nav["status"] = "active"
        nav["updated"] = _now_iso()[:10]
        _write_json(path, nav)

    except Exception:  # noqa: BLE001
        pass  # fail-open; memory growth must not affect execution path


# ── Repair memory growth ──────────────────────────────────────────────────────

def _repair_default(runtime_id: str) -> dict:
    return {
        "schema_version": "1.0",
        "layer": "C",
        "memory_family": "execution_repair",
        "runtime_id": runtime_id,
        "status": "seeded-empty",
        "updated_at": _now_iso(),
        "repair_patterns": [],
        "incident_candidates": [],
        "governance_boundary": (
            "Repair memory may inform preflight and operator review, "
            "but it cannot bypass Gate policy or apply self-repair without explicit authority."
        ),
    }


def record_repair_pattern(
    runtime_id: str,
    vault_root: Path,
    *,
    workflow_id: str,
    failure_context: str,
    repair_action: str,
    resolved: bool,
    notes: str = "",
) -> None:
    """
    Append a repair pattern entry to the repair memory for runtime_id.

    Repair patterns record a known failure mode and how it was resolved.
    They are advisory — the AOR engine does not auto-apply them.

    Fail-open — never raises.

    Parameters
    ----------
    runtime_id : str
        Runtime that experienced and resolved the failure.
    vault_root : Path
    workflow_id : str
        Workflow where the failure occurred.
    failure_context : str
        Brief description of what went wrong.
    repair_action : str
        What was done to repair it.
    resolved : bool
        Whether the repair was confirmed successful.
    notes : str
        Optional additional notes.
    """
    try:
        path = _repair_path(runtime_id, vault_root)
        repair = _load_json_or_default(path, _repair_default(runtime_id))

        patterns: list = list(repair.get("repair_patterns") or [])
        entry = {
            "workflow_id": workflow_id,
            "failure_context": failure_context,
            "repair_action": repair_action,
            "resolved": resolved,
            "notes": notes,
            "recorded_at": _now_iso(),
        }
        patterns.append(entry)
        if len(patterns) > _REPAIR_CAP:
            patterns = patterns[-_REPAIR_CAP:]

        repair["repair_patterns"] = patterns
        repair["status"] = "active"
        repair["updated_at"] = _now_iso()
        _write_json(path, repair)

    except Exception:  # noqa: BLE001
        pass  # fail-open


def record_incident_candidate(
    runtime_id: str,
    vault_root: Path,
    *,
    workflow_id: str,
    outcome: str,
    escalation_reason: Optional[str],
    notes: str = "",
) -> None:
    """
    Append an escalation or failure as an incident candidate to repair memory.

    Incident candidates are unresolved — they do not yet have a documented repair.
    They surface in operator briefings and drift scans for review.

    Fail-open — never raises.
    """
    try:
        path = _repair_path(runtime_id, vault_root)
        repair = _load_json_or_default(path, _repair_default(runtime_id))

        candidates: list = list(repair.get("incident_candidates") or [])
        entry = {
            "workflow_id": workflow_id,
            "outcome": outcome,
            "escalation_reason": escalation_reason or "",
            "notes": notes,
            "recorded_at": _now_iso(),
            "operator_reviewed": False,
        }
        candidates.append(entry)
        # Cap at the same limit as repair_patterns
        if len(candidates) > _REPAIR_CAP:
            candidates = candidates[-_REPAIR_CAP:]

        repair["incident_candidates"] = candidates
        repair["status"] = "active"
        repair["updated_at"] = _now_iso()
        _write_json(path, repair)

    except Exception:  # noqa: BLE001
        pass  # fail-open


# ── Memory export ─────────────────────────────────────────────────────────────

def export_memory_snapshot(runtime_id: str, vault_root: Path) -> dict[str, Any]:
    """
    Return a portable snapshot of all Layer C memory surfaces for runtime_id.

    This is a read-only export — it does not modify any files.
    Returns a dict with keys: runtime_id, profile, nav_map, repair_memory, scorecard.

    Fail-open: missing surfaces return their defaults.
    """
    from runtime.memory.inspector import (
        load_runtime_profile,
        load_nav_map,
        load_repair_memory,
    )
    from runtime.memory.scorecards.scorecard_updater import load_scorecard

    try:
        return {
            "runtime_id": runtime_id,
            "exported_at": _now_iso(),
            "profile": load_runtime_profile(runtime_id, vault_root),
            "nav_map": load_nav_map(runtime_id, vault_root),
            "repair_memory": load_repair_memory(runtime_id, vault_root),
            "scorecard": load_scorecard(runtime_id, vault_root),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "runtime_id": runtime_id,
            "exported_at": _now_iso(),
            "export_error": str(exc),
        }


# ── Execution outcome dispatch ────────────────────────────────────────────────

def apply_execution_to_memory(
    runtime_id: str,
    vault_root: Path,
    audit_record: dict[str, Any],
) -> None:
    """
    Apply a completed AOR execution to all relevant memory surfaces.

    This is the single call-site that engine.py uses after each execution.
    It orchestrates:
      1. warm_nav_map_from_execution — nav-map route + trigger recording
      2. record_incident_candidate — for escalated/failed outcomes only

    Fail-open — never raises; all sub-calls are themselves fail-open.
    """
    try:
        warm_nav_map_from_execution(runtime_id, vault_root, audit_record)

        status = audit_record.get("status") or ""
        if status in ("escalated", "failed"):
            record_incident_candidate(
                runtime_id,
                vault_root,
                workflow_id=audit_record.get("workflow_id") or "",
                outcome=status,
                escalation_reason=audit_record.get("escalation_reason"),
            )
    except Exception:  # noqa: BLE001
        pass  # fail-open
