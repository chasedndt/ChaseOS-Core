"""
scorecard_updater.py — ChaseOS Phase 9 Feature 13
Agent Scorecards

Tracks runtime performance history across AOR workflow executions.
Each runtime (openclaw, hermes, etc.) gets its own scorecard JSON at:
    runtime/memory/scorecards/[runtime_id].json

Scorecards are factual performance records — they record outcomes,
overreach events, and CGL violations. They do NOT autonomously reduce
permissions. All permission changes require explicit operator instruction.

Scorecard schema:
    runtime_id:       str — runtime that executed the workflow
    executions[]:     list of per-execution records
        audit_id:         str — links to 07_LOGS/Agent-Activity/*.json
        workflow_id:      str
        outcome:          "success" | "escalated" | "failed" | "dry_run_ok"
        overreach_events: list[str] — permission boundary violations
        cgl_violations:   list[dict] — CGL blocked/restricted events
        operator_acceptance: bool | null — unknown until operator review
        timestamp_utc:    str
    aggregate_stats:  computed from executions
        total_executions:  int
        success_count:     int
        escalated_count:   int
        failed_count:      int
        reliability_rate:  float — success_count / total_executions
        overreach_rate:    float — executions with overreach / total
        compliance_rate:   float — executions with zero CGL violations / total
        last_updated:      str

Public API:
    update_scorecard(runtime_id, audit_record, vault_root) -> None
    get_scorecard(runtime_id, vault_root) -> dict
    load_scorecard(runtime_id, vault_root) -> dict
    scorecard_summary_text(runtime_id, vault_root) -> str
    list_scorecards(vault_root) -> list[str]
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


# ── Constants ─────────────────────────────────────────────────────────────────

_SCORECARD_DIR = Path("runtime/memory/scorecards")
_MAX_EXECUTIONS_STORED = 200  # cap; oldest entries are dropped when exceeded


# ── Scorecard structure ───────────────────────────────────────────────────────


def _empty_scorecard(runtime_id: str) -> dict[str, Any]:
    return {
        "runtime_id": runtime_id,
        "schema_version": "1.0",
        "executions": [],
        "aggregate_stats": _compute_stats([]),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }


def _compute_stats(executions: list[dict]) -> dict[str, Any]:
    total = len(executions)
    if total == 0:
        return {
            "total_executions": 0,
            "success_count": 0,
            "escalated_count": 0,
            "failed_count": 0,
            "dry_run_count": 0,
            "reliability_rate": 0.0,
            "overreach_rate": 0.0,
            "compliance_rate": 0.0,
        }

    success_count = sum(1 for e in executions if e.get("outcome") == "success")
    escalated_count = sum(1 for e in executions if e.get("outcome") == "escalated")
    failed_count = sum(1 for e in executions if e.get("outcome") == "failed")
    dry_run_count = sum(1 for e in executions if e.get("outcome") == "dry_run_ok")

    overreach_count = sum(
        1 for e in executions if len(e.get("overreach_events", [])) > 0
    )
    cgl_violation_count = sum(
        1 for e in executions if len(e.get("cgl_violations", [])) > 0
    )

    # Reliability = success / non-dry-run executions
    non_dry_total = total - dry_run_count
    reliability = (success_count / non_dry_total) if non_dry_total > 0 else 0.0
    overreach_rate = (overreach_count / total) if total > 0 else 0.0
    compliance_rate = (1.0 - cgl_violation_count / total) if total > 0 else 1.0

    return {
        "total_executions": total,
        "success_count": success_count,
        "escalated_count": escalated_count,
        "failed_count": failed_count,
        "dry_run_count": dry_run_count,
        "reliability_rate": round(reliability, 4),
        "overreach_rate": round(overreach_rate, 4),
        "compliance_rate": round(compliance_rate, 4),
    }


def _scorecard_path(runtime_id: str, vault_root: Path) -> Path:
    return vault_root / _SCORECARD_DIR / f"{runtime_id}.json"


# ── Public API ────────────────────────────────────────────────────────────────


def load_scorecard(runtime_id: str, vault_root: Path) -> dict[str, Any]:
    """
    Load the scorecard JSON for runtime_id.
    Returns a fresh empty scorecard if the file does not exist.
    Never raises — fail-open.
    """
    try:
        path = _scorecard_path(runtime_id, vault_root)
        if not path.exists():
            return _empty_scorecard(runtime_id)
        text = path.read_text(encoding="utf-8", errors="replace")
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            return _empty_scorecard(runtime_id)
        return parsed
    except Exception:  # noqa: BLE001
        return _empty_scorecard(runtime_id)


def get_scorecard(runtime_id: str, vault_root: Path) -> dict[str, Any]:
    """Alias for load_scorecard — reads current scorecard state."""
    return load_scorecard(runtime_id, vault_root)


def _extract_execution_entry(audit_record: dict[str, Any]) -> dict[str, Any]:
    """
    Build an execution entry from an AOR audit record dict.

    audit_record is the dict written by _write_audit_record() in engine.py.
    Fields consumed:
        audit_id, workflow_id, status (→ outcome), timestamp_utc
        outputs.writeback.cgl_violations (from Stage 5 data, if present)
        escalation_reason (if escalated — captured as overreach_event when relevant)
    """
    audit_id = audit_record.get("audit_id", "")
    workflow_id = audit_record.get("workflow_id", "")
    outcome = audit_record.get("status", "unknown")
    timestamp = audit_record.get("timestamp_utc", datetime.now(timezone.utc).isoformat())

    # Extract CGL violations from Stage 5 data stored in outputs
    outputs = audit_record.get("outputs", {}) or {}
    run_outputs = outputs.get("run", {}) or {}
    cgl_violations = list(run_outputs.get("cgl_violations", []) or [])

    # Extract overreach events:
    # Currently AOR records escalation reasons. A permission_ceiling escalation
    # is classified as an overreach event; other escalation reasons are not.
    overreach_events: list[str] = []
    stage_reached = audit_record.get("stage_reached", "")
    escalation_reason = audit_record.get("escalation_reason") or ""

    if stage_reached == "permission_ceiling" and escalation_reason:
        overreach_events.append(escalation_reason)

    return {
        "audit_id": audit_id,
        "workflow_id": workflow_id,
        "outcome": outcome,
        "overreach_events": overreach_events,
        "cgl_violations": cgl_violations,
        "operator_acceptance": None,  # unknown until operator review
        "timestamp_utc": timestamp,
        "stage_reached": stage_reached,
    }


def update_scorecard(
    runtime_id: str,
    audit_record: dict[str, Any],
    vault_root: Path,
) -> None:
    """
    Update the scorecard for runtime_id with a new execution record from audit_record.

    Appends the execution entry, recomputes aggregate_stats, and writes the scorecard.
    Never raises — scorecard update failure must not affect the primary execution path.

    Parameters
    ----------
    runtime_id : str
        ID of the runtime that executed the workflow ("openclaw", "hermes", etc.)
    audit_record : dict
        The AOR audit record dict (as written by _write_audit_record in engine.py).
    vault_root : Path
        Vault root path.
    """
    try:
        scorecard = load_scorecard(runtime_id, vault_root)
        executions: list[dict] = list(scorecard.get("executions", []) or [])

        entry = _extract_execution_entry(audit_record)
        executions.append(entry)

        # Cap execution history
        if len(executions) > _MAX_EXECUTIONS_STORED:
            executions = executions[-_MAX_EXECUTIONS_STORED:]

        scorecard["executions"] = executions
        scorecard["aggregate_stats"] = _compute_stats(executions)
        scorecard["last_updated"] = datetime.now(timezone.utc).isoformat()
        scorecard["runtime_id"] = runtime_id

        path = _scorecard_path(runtime_id, vault_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(scorecard, indent=2, default=str), encoding="utf-8")

    except Exception:  # noqa: BLE001
        pass  # Scorecard update is best-effort; never propagate


def scorecard_summary_text(runtime_id: str, vault_root: Path) -> str:
    """
    Return a human-readable scorecard summary for use in operator briefings.

    Format:
        Agent Scorecard: {runtime_id}
          Executions: {total} | Success: {n} | Escalated: {n} | Failed: {n}
          Reliability: {pct}% | Overreach rate: {pct}% | CGL compliance: {pct}%
          Last execution: {workflow_id} → {outcome} ({timestamp})
    """
    try:
        sc = load_scorecard(runtime_id, vault_root)
        stats = sc.get("aggregate_stats", {})
        executions = sc.get("executions", [])

        total = stats.get("total_executions", 0)
        if total == 0:
            return f"Agent Scorecard: {runtime_id}\n  No executions recorded yet."

        reliability = stats.get("reliability_rate", 0.0)
        overreach = stats.get("overreach_rate", 0.0)
        compliance = stats.get("compliance_rate", 0.0)

        last = executions[-1] if executions else {}
        last_wf = last.get("workflow_id", "—")
        last_outcome = last.get("outcome", "—")
        last_ts = last.get("timestamp_utc", "")[:19].replace("T", " ")

        lines = [
            f"Agent Scorecard: {runtime_id}",
            f"  Executions: {total} | "
            f"Success: {stats.get('success_count', 0)} | "
            f"Escalated: {stats.get('escalated_count', 0)} | "
            f"Failed: {stats.get('failed_count', 0)}",
            f"  Reliability: {reliability * 100:.1f}% | "
            f"Overreach rate: {overreach * 100:.1f}% | "
            f"CGL compliance: {compliance * 100:.1f}%",
            f"  Last: {last_wf} -> {last_outcome} ({last_ts} UTC)",
        ]
        return "\n".join(lines)
    except Exception:  # noqa: BLE001
        return f"Agent Scorecard: {runtime_id}\n  (scorecard unavailable)"


def list_scorecards(vault_root: Path) -> list[str]:
    """
    Return a list of runtime_ids that have scorecard files.
    """
    try:
        sc_dir = vault_root / _SCORECARD_DIR
        if not sc_dir.exists():
            return []
        return [
            p.stem
            for p in sorted(sc_dir.glob("*.json"))
            if p.is_file()
        ]
    except Exception:  # noqa: BLE001
        return []


def mark_operator_acceptance(
    runtime_id: str,
    audit_id: str,
    accepted: bool,
    vault_root: Path,
) -> bool:
    """
    Mark an execution entry's operator_acceptance field.

    Returns True if the entry was found and updated, False otherwise.
    Never raises.
    """
    try:
        sc = load_scorecard(runtime_id, vault_root)
        executions = sc.get("executions", [])
        found = False
        for entry in executions:
            if entry.get("audit_id") == audit_id:
                entry["operator_acceptance"] = accepted
                found = True
                break
        if found:
            sc["executions"] = executions
            sc["aggregate_stats"] = _compute_stats(executions)
            sc["last_updated"] = datetime.now(timezone.utc).isoformat()
            path = _scorecard_path(runtime_id, vault_root)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(sc, indent=2, default=str), encoding="utf-8")
        return found
    except Exception:  # noqa: BLE001
        return False
