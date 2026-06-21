"""Policy validator and dry-run call builder for n8n MCP workflow exposure."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - fallback covered by simple parser users
    yaml = None

from runtime.common.simple_yaml import parse_simple_yaml as _parse_simple_yaml


REQUIRED_WORKFLOW_FIELDS = {
    "workflow_id",
    "purpose",
    "exposed_to_mcp",
    "trigger_type",
    "approval_required",
    "allowed_callers",
    "reads",
    "writes",
    "secrets_required",
    "current_status",
}

FORBIDDEN_PURPOSE_TERMS = {
    "live trading",
    "trade execution",
    "wallet signing",
    "exchange signing",
    "credential export",
}


class N8NWorkflowPolicyError(ValueError):
    """Raised when an n8n workflow registry or call draft violates policy."""


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_workflow_registry(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if yaml is not None:
        data = yaml.safe_load(text)
    else:
        data = _parse_simple_yaml(text)
    if not isinstance(data, dict):
        raise N8NWorkflowPolicyError("workflow registry must parse as a mapping")
    return data


def validate_registry(registry: dict[str, Any]) -> dict[str, Any]:
    workflows = registry.get("workflows")
    errors: list[str] = []
    if not isinstance(workflows, list):
        return {"ok": False, "errors": ["workflows must be a list"], "workflow_count": 0}

    seen: set[str] = set()
    for index, workflow in enumerate(workflows):
        if not isinstance(workflow, dict):
            errors.append(f"workflow[{index}] must be a mapping")
            continue
        missing = sorted(REQUIRED_WORKFLOW_FIELDS.difference(workflow))
        if missing:
            errors.append(f"{workflow.get('workflow_id', index)} missing fields: {missing}")
        workflow_id = workflow.get("workflow_id")
        if not isinstance(workflow_id, str) or not workflow_id:
            errors.append(f"workflow[{index}] workflow_id is required")
        elif workflow_id in seen:
            errors.append(f"duplicate workflow_id: {workflow_id}")
        else:
            seen.add(workflow_id)
        if workflow.get("exposed_to_mcp") is True and workflow.get("approval_required") is not True:
            errors.append(f"{workflow_id} exposed_to_mcp requires approval_required=true")
        errors.extend(validate_workflow_policy(workflow).get("errors", []))

    return {"ok": not errors, "errors": errors, "workflow_count": len(workflows)}


def validate_workflow_policy(
    workflow: dict[str, Any],
    *,
    production: bool = False,
    approved: bool = False,
) -> dict[str, Any]:
    errors: list[str] = []
    workflow_id = workflow.get("workflow_id", "<unknown>")
    purpose_text = " ".join(
        str(workflow.get(field, "")).lower()
        for field in ("workflow_id", "purpose", "current_status")
    )
    if any(term in purpose_text for term in FORBIDDEN_PURPOSE_TERMS):
        if workflow.get("current_status") != "blocked" or workflow.get("exposed_to_mcp") is not False:
            errors.append(f"{workflow_id} forbidden action must remain blocked and not exposed")
    if production and workflow.get("approval_required") and not approved:
        errors.append(f"{workflow_id} production call requires approval")
    if production and workflow.get("current_status") in {"planned", "dry_run_candidate", "blocked"}:
        errors.append(f"{workflow_id} is not production-enabled")
    if workflow.get("exposed_to_mcp") is True and not workflow.get("allowed_callers"):
        errors.append(f"{workflow_id} exposed workflow must declare allowed_callers")
    return {"ok": not errors, "errors": errors}


def build_n8n_call_draft(
    *,
    workflow_id: str,
    registry_path: Path,
    caller: str,
    payload: dict[str, Any] | None = None,
    production: bool = False,
    approved: bool = False,
) -> dict[str, Any]:
    """Build a dry-run n8n workflow call draft without making HTTP calls."""
    registry = load_workflow_registry(registry_path)
    verdict = validate_registry(registry)
    if not verdict["ok"]:
        raise N8NWorkflowPolicyError("; ".join(verdict["errors"]))

    workflows = registry["workflows"]
    selected = next((item for item in workflows if item.get("workflow_id") == workflow_id), None)
    if selected is None:
        raise N8NWorkflowPolicyError(f"unknown workflow_id: {workflow_id}")
    if selected.get("current_status") == "blocked":
        raise N8NWorkflowPolicyError(f"{workflow_id} is blocked and cannot be drafted")
    if caller not in selected.get("allowed_callers", []):
        raise N8NWorkflowPolicyError(f"caller {caller!r} is not allowed for {workflow_id}")

    policy = validate_workflow_policy(selected, production=production, approved=approved)
    if not policy["ok"]:
        raise N8NWorkflowPolicyError("; ".join(policy["errors"]))

    return {
        "dry_run": True,
        "live_http_call": False,
        "workflow_id": workflow_id,
        "caller": caller,
        "created_at_utc": _utc_stamp(),
        "trigger_type": selected.get("trigger_type"),
        "approval_required": bool(selected.get("approval_required")),
        "production_requested": production,
        "approved": approved,
        "payload": payload or {},
        "policy": {
            "reads": selected.get("reads", []),
            "writes": selected.get("writes", []),
            "secrets_required": selected.get("secrets_required", []),
            "current_status": selected.get("current_status"),
        },
    }


def write_n8n_call_draft(draft: dict[str, Any], *, vault_root: Path, descriptor: str) -> Path:
    if draft.get("dry_run") is not True or draft.get("live_http_call") is not False:
        raise N8NWorkflowPolicyError("only dry-run n8n drafts may be written")
    out_dir = vault_root / "07_LOGS" / "Agent-Activity" / "_dry_run_payloads"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{descriptor}.json"
    out_path.write_text(json.dumps(draft, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out_path
