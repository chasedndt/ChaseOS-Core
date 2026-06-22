"""
runtime.operator_surface.audit

Audit artifact serialization and persistence for FSOS runs.
Every run produces an OperatorRunAudit; this module writes it to
07_LOGS/Agent-Activity/ and provides replay reconstruction.

Audit model defined in: 06_AGENTS/Full-System-Operator-Surface.md Section 6.4
SOP: 04_SOPS/Full-System-Operator-Safety-SOP.md Section 9
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from runtime.operator_surface.contracts import OperatorRunAudit
from runtime.operator_surface.events import OperatorEvent, OperatorEventType


def _get_vault_root() -> Path:
    """Resolve vault root — same logic as other ChaseOS runtime modules."""
    vault_root = os.environ.get("CHASEOS_VAULT_ROOT")
    if vault_root:
        return Path(vault_root)
    # Walk up from this file to find the vault root (contains CLAUDE.md)
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "CLAUDE.md").exists():
            return parent
    raise RuntimeError("Cannot resolve vault root. Set CHASEOS_VAULT_ROOT env var.")


def get_audit_dir(vault_root: Optional[Path] = None) -> Path:
    """Return the audit log directory, creating it if needed."""
    root = vault_root or _get_vault_root()
    audit_dir = root / "07_LOGS" / "Agent-Activity"
    audit_dir.mkdir(parents=True, exist_ok=True)
    return audit_dir


def write_audit(audit: OperatorRunAudit, vault_root: Optional[Path] = None) -> Path:
    """
    Serialize and write an OperatorRunAudit to 07_LOGS/Agent-Activity/.
    Returns the path of the written file.

    File naming: YYYY-MM-DD_HHMMSS_operator_{surface}_{run_id[:8]}.json
    """
    audit_dir = get_audit_dir(vault_root)
    now = datetime.now(timezone.utc)
    datestamp = now.strftime("%Y-%m-%d_%H%M%S")
    surface = audit.surface or "unknown"
    run_short = audit.run_id[:8] if audit.run_id else "norun"
    filename = f"{datestamp}_operator_{surface}_{run_short}.json"
    filepath = audit_dir / filename

    # Build serializable dict
    audit_dict = {
        "run_id": audit.run_id,
        "workflow_id": audit.workflow_id,
        "surface": audit.surface,
        "outcome": audit.outcome,
        "started_at": audit.started_at,
        "completed_at": audit.completed_at,
        "steps_planned": audit.steps_planned,
        "steps_completed": audit.steps_completed,
        "steps_failed": audit.steps_failed,
        "actions_taken": audit.actions_taken,
        "approvals_required": audit.approvals_required,
        "approvals_granted": audit.approvals_granted,
        "approvals_denied": audit.approvals_denied,
        "recovery_attempts": audit.recovery_attempts,
        "vault_writes": audit.vault_writes,
        "capture_ids": audit.capture_ids,
        "error": audit.error,
        "scope": _scope_to_dict(audit.scope),
        "plan": audit.plan,
        "events": [e.to_dict() for e in audit.events],
        "approvals": [_approval_to_dict(a) for a in audit.approvals],
        "adapter_payload": audit.adapter_payload,
    }

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(audit_dict, f, indent=2, ensure_ascii=False)

    return filepath


def load_audit(run_id: str, vault_root: Optional[Path] = None) -> Optional[dict]:
    """
    Load an audit artifact by run_id prefix.
    Returns the parsed JSON dict or None if not found.
    Used for replay and post-mortem analysis.
    """
    audit_dir = get_audit_dir(vault_root)
    for filepath in sorted(audit_dir.glob("*.json")):
        try:
            with open(filepath, encoding="utf-8") as f:
                data = json.load(f)
            if data.get("run_id", "").startswith(run_id):
                return data
        except Exception:
            continue
    return None


def reconstruct_event_sequence(audit_data: dict) -> list[OperatorEvent]:
    """
    Reconstruct the event sequence from an audit artifact dict.
    Used for replay — read-only; does not re-execute.
    """
    events = []
    for e in audit_data.get("events", []):
        try:
            event = OperatorEvent(
                event_id=e.get("event_id", ""),
                run_id=e.get("run_id", ""),
                surface=e.get("surface", ""),
                event_type=OperatorEventType(e.get("event_type", "step_started")),
                timestamp=e.get("timestamp", ""),
                step_index=e.get("step_index", 0),
                action_class=e.get("action_class"),
                description=e.get("description", ""),
                payload=e.get("payload", {}),
                approval_required=e.get("approval_required", False),
                approval_id=e.get("approval_id"),
                grounding_mode=e.get("grounding_mode"),
            )
            events.append(event)
        except Exception:
            continue
    return events


def _scope_to_dict(scope) -> Optional[dict]:
    if scope is None:
        return None
    return {
        "run_id": scope.run_id,
        "surface": scope.surface.value if hasattr(scope.surface, "value") else scope.surface,
        "target_uris": scope.target_uris,
        "allowed_origins": scope.allowed_origins,
        "allowed_paths": scope.allowed_paths,
        "forbidden_zones": scope.forbidden_zones,
        "max_actions": scope.max_actions,
        "max_duration_seconds": scope.max_duration_seconds,
        "requires_approval": scope.requires_approval,
        "external_network": scope.external_network,
        "credential_access": scope.credential_access,
    }


def _approval_to_dict(a) -> dict:
    if hasattr(a, "__dataclass_fields__"):
        import dataclasses
        return dataclasses.asdict(a)
    return dict(a)
