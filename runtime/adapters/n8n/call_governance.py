"""Approval-aware dry-run call governance for n8n workflow exposure."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.adapters.n8n.workflow_policy import (
    N8NWorkflowPolicyError,
    build_n8n_call_draft,
    load_workflow_registry,
    validate_registry,
)


APPROVAL_DIR = Path("07_LOGS/Agent-Activity/_n8n_approvals")
CALL_DRAFT_DIR = Path("07_LOGS/Agent-Activity/_n8n_call_drafts")
SECRET_KEY_TERMS = {
    "secret",
    "token",
    "password",
    "apikey",
    "api_key",
    "credential",
    "privatekey",
    "private_key",
    "accesskey",
    "access_key",
}
DECISIONS = {"approved", "denied", "revoked"}
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]+$")


class N8NCallGovernanceError(N8NWorkflowPolicyError):
    """Raised when an n8n governed call draft violates approval/audit policy."""


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _utc_filename_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-")
    return slug or "n8n-call"


def _ensure_child(parent: Path, child: Path) -> Path:
    parent_resolved = parent.resolve()
    child_resolved = child.resolve()
    try:
        child_resolved.relative_to(parent_resolved)
    except ValueError as exc:
        raise N8NCallGovernanceError(f"path escapes expected directory: {child}") from exc
    return child_resolved


def _approval_dir(vault_root: Path) -> Path:
    return _ensure_child(vault_root, vault_root / APPROVAL_DIR)


def _draft_dir(vault_root: Path) -> Path:
    return _ensure_child(vault_root, vault_root / CALL_DRAFT_DIR)


def _approval_request_path(vault_root: Path, approval_id: str) -> Path:
    _validate_approval_id(approval_id)
    return _ensure_child(_approval_dir(vault_root), vault_root / APPROVAL_DIR / f"{approval_id}-request.json")


def _approval_decision_path(vault_root: Path, approval_id: str) -> Path:
    _validate_approval_id(approval_id)
    return _ensure_child(_approval_dir(vault_root), vault_root / APPROVAL_DIR / f"{approval_id}-decision.json")


def _validate_approval_id(approval_id: str) -> None:
    if not _SAFE_ID.match(approval_id):
        raise N8NCallGovernanceError(f"unsafe approval_id: {approval_id!r}")


def _normalize_key(key: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(key).lower())


def _find_secret_like_keys(value: Any, *, prefix: str = "payload") -> list[str]:
    matches: list[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            path = f"{prefix}.{key}"
            normalized = _normalize_key(key)
            if any(_normalize_key(term) in normalized for term in SECRET_KEY_TERMS):
                matches.append(path)
            matches.extend(_find_secret_like_keys(nested, prefix=path))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            matches.extend(_find_secret_like_keys(nested, prefix=f"{prefix}[{index}]"))
    return matches


def assert_payload_has_no_secret_like_keys(payload: dict[str, Any] | None) -> None:
    """Reject payloads that would risk logging credential-shaped fields."""
    if payload is None:
        return
    if not isinstance(payload, dict):
        raise N8NCallGovernanceError("n8n governed call payload must be a mapping")
    matches = _find_secret_like_keys(payload)
    if matches:
        raise N8NCallGovernanceError(
            "n8n governed call payload contains secret-like keys: " + ", ".join(sorted(matches))
        )


def _payload_digest(payload: dict[str, Any] | None) -> str:
    canonical = json.dumps(payload or {}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _load_workflow(registry_path: Path, workflow_id: str) -> dict[str, Any]:
    registry = load_workflow_registry(registry_path)
    verdict = validate_registry(registry)
    if not verdict["ok"]:
        raise N8NCallGovernanceError("; ".join(verdict["errors"]))
    workflows = registry.get("workflows", [])
    selected = next((item for item in workflows if item.get("workflow_id") == workflow_id), None)
    if selected is None:
        raise N8NCallGovernanceError(f"unknown workflow_id: {workflow_id}")
    return selected


def _validate_workflow_caller(workflow: dict[str, Any], workflow_id: str, caller: str) -> None:
    allowed = workflow.get("allowed_callers", [])
    if caller not in allowed:
        raise N8NCallGovernanceError(f"caller {caller!r} is not allowed for {workflow_id}")


def create_approval_request(
    *,
    vault_root: Path,
    registry_path: Path,
    workflow_id: str,
    caller: str,
    requested_by: str,
    reason: str,
    payload: dict[str, Any] | None = None,
    production_requested: bool = True,
    expires_at_utc: str | None = None,
) -> dict[str, Any]:
    """Create an auditable pending approval request without storing payload values."""
    assert_payload_has_no_secret_like_keys(payload)
    workflow = _load_workflow(registry_path, workflow_id)
    _validate_workflow_caller(workflow, workflow_id, caller)

    approval_id = f"n8n-appr-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"
    record = {
        "schema_version": "1.0",
        "record_type": "n8n_approval_request",
        "approval_id": approval_id,
        "workflow_id": workflow_id,
        "caller": caller,
        "requested_by": requested_by,
        "requested_at_utc": _utc_stamp(),
        "reason": reason,
        "production_requested": production_requested,
        "approval_required": bool(workflow.get("approval_required")),
        "workflow_status": workflow.get("current_status"),
        "payload_digest_sha256": _payload_digest(payload),
        "payload_values_logged": False,
        "credential_values_logged": False,
        "live_http_call": False,
        "canonical_writeback": False,
        "expires_at_utc": expires_at_utc,
    }

    out_dir = _approval_dir(vault_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = _approval_request_path(vault_root, approval_id)
    out_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {**record, "path": str(out_path)}


def load_approval_request(*, vault_root: Path, approval_id: str) -> dict[str, Any]:
    path = _approval_request_path(vault_root, approval_id)
    if not path.exists():
        raise N8NCallGovernanceError(f"approval request not found: {approval_id}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise N8NCallGovernanceError(f"approval request is not a mapping: {approval_id}")
    return data


def record_approval_decision(
    *,
    vault_root: Path,
    approval_id: str,
    workflow_id: str,
    caller: str,
    decision: str,
    decided_by: str,
    reason: str,
) -> dict[str, Any]:
    """Record one immutable operator decision for an n8n approval request."""
    if decision not in DECISIONS:
        raise N8NCallGovernanceError(f"unsupported approval decision: {decision}")
    request = load_approval_request(vault_root=vault_root, approval_id=approval_id)
    if request.get("workflow_id") != workflow_id:
        raise N8NCallGovernanceError("approval decision workflow_id does not match request")
    if request.get("caller") != caller:
        raise N8NCallGovernanceError("approval decision caller does not match request")

    out_dir = _approval_dir(vault_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = _approval_decision_path(vault_root, approval_id)
    if out_path.exists():
        raise N8NCallGovernanceError(f"approval decision already recorded: {approval_id}")

    record = {
        "schema_version": "1.0",
        "record_type": "n8n_approval_decision",
        "approval_id": approval_id,
        "workflow_id": workflow_id,
        "caller": caller,
        "decision": decision,
        "decided_by": decided_by,
        "decided_at_utc": _utc_stamp(),
        "reason": reason,
        "credential_values_logged": False,
        "live_http_call": False,
        "canonical_writeback": False,
    }
    out_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {**record, "path": str(out_path)}


def load_approval_decision(*, vault_root: Path, approval_id: str) -> dict[str, Any] | None:
    path = _approval_decision_path(vault_root, approval_id)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise N8NCallGovernanceError(f"approval decision is not a mapping: {approval_id}")
    return data


def resolve_approval_state(
    *,
    vault_root: Path,
    approval_id: str | None,
    workflow_id: str,
    caller: str,
) -> dict[str, Any]:
    """Resolve whether an approval id authorizes the requested workflow/caller pair."""
    if not approval_id:
        return {
            "approval_id": None,
            "state": "missing",
            "approved": False,
            "reasons": ["approval_id not supplied"],
        }

    request = load_approval_request(vault_root=vault_root, approval_id=approval_id)
    if request.get("workflow_id") != workflow_id:
        raise N8NCallGovernanceError("approval request workflow_id does not match call draft")
    if request.get("caller") != caller:
        raise N8NCallGovernanceError("approval request caller does not match call draft")

    decision = load_approval_decision(vault_root=vault_root, approval_id=approval_id)
    if decision is None:
        return {
            "approval_id": approval_id,
            "state": "pending",
            "approved": False,
            "reasons": ["approval decision not recorded"],
        }
    if decision.get("workflow_id") != workflow_id:
        raise N8NCallGovernanceError("approval decision workflow_id does not match call draft")
    if decision.get("caller") != caller:
        raise N8NCallGovernanceError("approval decision caller does not match call draft")

    state = str(decision.get("decision"))
    reasons: list[str] = []
    approved = state == "approved"
    expires_at = request.get("expires_at_utc")
    if approved and expires_at:
        expires_dt = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
        if expires_dt < datetime.now(timezone.utc):
            approved = False
            state = "expired"
            reasons.append("approval request expired")
    if not reasons:
        reasons.append(f"approval decision is {decision.get('decision')}")
    return {
        "approval_id": approval_id,
        "state": state,
        "approved": approved,
        "reasons": reasons,
        "requested_at_utc": request.get("requested_at_utc"),
        "decided_at_utc": decision.get("decided_at_utc"),
    }


def build_governed_call_draft(
    *,
    vault_root: Path,
    registry_path: Path,
    workflow_id: str,
    caller: str,
    payload: dict[str, Any] | None = None,
    production: bool = False,
    approval_id: str | None = None,
) -> dict[str, Any]:
    """Build a dry-run n8n call draft with explicit approval-state metadata."""
    assert_payload_has_no_secret_like_keys(payload)
    workflow = _load_workflow(registry_path, workflow_id)
    _validate_workflow_caller(workflow, workflow_id, caller)
    approval_required = bool(workflow.get("approval_required"))
    approval_state = (
        resolve_approval_state(
            vault_root=vault_root,
            approval_id=approval_id,
            workflow_id=workflow_id,
            caller=caller,
        )
        if approval_required
        else {"approval_id": approval_id, "state": "not_required", "approved": True, "reasons": []}
    )
    approved = bool(approval_state.get("approved"))
    if production and approval_required and not approved:
        raise N8NCallGovernanceError(
            f"{workflow_id} production call requires an approved approval record; "
            f"current approval state: {approval_state.get('state')}"
        )

    try:
        draft = build_n8n_call_draft(
            workflow_id=workflow_id,
            registry_path=registry_path,
            caller=caller,
            payload=payload,
            production=production,
            approved=approved,
        )
    except N8NWorkflowPolicyError as exc:
        raise N8NCallGovernanceError(str(exc)) from exc

    draft["governance"] = {
        "schema_version": "1.0",
        "policy_layer": "n8n_call_governance",
        "approval_id": approval_id,
        "approval_required": approval_required,
        "approval_state": approval_state.get("state"),
        "approval_approved": approved,
        "approval_reasons": approval_state.get("reasons", []),
        "can_execute_live": False,
        "http_execution_enabled": False,
        "live_blocked_reasons": [
            "governance_helper_emits_dry_run_drafts_only",
            "live_n8n_execution_requires_separate_configured_runner",
        ],
        "credential_values_logged": False,
        "secret_like_payload_keys_allowed": False,
        "canonical_writeback": False,
        "audit_target": str(CALL_DRAFT_DIR),
    }
    return draft


def write_governed_call_draft(
    draft: dict[str, Any],
    *,
    vault_root: Path,
    descriptor: str | None = None,
) -> Path:
    """Write an approval-aware n8n call draft to the ChaseOS audit surface."""
    governance = draft.get("governance")
    if not isinstance(governance, dict):
        raise N8NCallGovernanceError("governed n8n call draft requires governance metadata")
    if draft.get("dry_run") is not True or draft.get("live_http_call") is not False:
        raise N8NCallGovernanceError("only dry-run n8n call drafts may be written")
    if governance.get("can_execute_live") is not False:
        raise N8NCallGovernanceError("governed n8n call draft must not be live-executable")
    assert_payload_has_no_secret_like_keys(draft.get("payload") or {})

    out_dir = _draft_dir(vault_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = descriptor or str(draft.get("workflow_id") or "n8n-call")
    filename = f"{_utc_filename_stamp()}-{_safe_slug(suffix)}.json"
    out_path = _ensure_child(out_dir, out_dir / filename)
    out_path.write_text(json.dumps(draft, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out_path

