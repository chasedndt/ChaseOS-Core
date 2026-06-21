"""Mission Mode Agent Bus task packet contract.

This module validates the narrow packet shape used to preview VentureOps
Mission Mode dry-review work on the ChaseOS Agent Bus. It does not enqueue
tasks or grant authority; callers still use runtime.agent_bus.bus for live bus
writes after a separate approval path exists.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any


MISSION_TASK_TYPE = "mission.run_dry_review"
MISSION_INTENT = "TASK"
MISSION_STATUS = "open"
MISSION_WRITE_POLICY = "declared-paths"

ADDRESSABLE_MISSION_RUNTIMES = ("Codex", "Hermes", "OpenClaw")
MISSION_TASK_SENDERS = ("Operator", "OpenClaw", "Hermes", "Codex")
MISSION_ALLOWED_RESULT_SHAPES = ("proposal", "patch", "risk", "blocked", "complete")

MISSION_ALLOWED_WRITE_ROOTS = (
    "07_LOGS/VentureOps-Missions/",
    "07_LOGS/Workflow-Proofs/",
    "07_LOGS/Runtime-Audits/",
)

MISSION_FORBIDDEN_ACTIONS = (
    "mission_activation",
    "aor_dispatch",
    "agent_bus_task_write",
    "workflow_evolution_apply",
    "provider_call",
    "browser_action",
    "browser_skill_activation",
    "external_send",
    "crm_or_payment_mutation",
    "live_trading",
    "protected_file_edit",
    "canonical_promotion",
    "credential_or_secret_read",
)

REQUIRED_BASE_FIELDS = (
    "task_id",
    "run_id",
    "from",
    "to",
    "intent",
    "status",
    "request",
    "expected_output",
    "created_at",
    "updated_at",
)

REQUIRED_MISSION_FIELDS = (
    "task_type",
    "mission_id",
    "mission_workspace_path",
    "activation_approval_packet_path",
    "write_policy",
    "forbidden_actions",
    "allowed_result_shapes",
    "execution_constraints",
    "agent_bus_task_written",
    "activation_performed",
    "workflow_evolution_applied",
)

_ALLOWED_PRIORITIES = {"low", "normal", "high", "critical"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-")
    return slug[:96] or "mission"


def _normalize_vault_relative_path(value: Any, field_name: str, errors: list[str]) -> str:
    raw = str(value or "").strip()
    if not raw:
        errors.append(f"{field_name} must be a non-empty vault-relative path")
        return ""

    normalized = raw.replace("\\", "/").strip()
    windows_path = PureWindowsPath(raw)
    posix_path = PurePosixPath(normalized)
    if windows_path.drive or windows_path.root or posix_path.is_absolute():
        errors.append(f"{field_name} must be relative to the vault root")
        return normalized

    parts = [part for part in normalized.split("/") if part and part != "."]
    if not parts:
        errors.append(f"{field_name} may not target the vault root directly")
        return ""
    if any(part == ".." for part in parts):
        errors.append(f"{field_name} may not leave the vault root")
        return "/".join(parts)

    result = "/".join(parts)
    if normalized.endswith("/"):
        result += "/"
    return result


def _path_within_allowed_roots(path: str, roots: tuple[str, ...]) -> bool:
    candidate = path.rstrip("/")
    for root in roots:
        normalized_root = root.rstrip("/")
        if candidate == normalized_root or candidate.startswith(normalized_root + "/"):
            return True
    return False


def _validate_non_empty_string(packet: dict[str, Any], field: str, errors: list[str]) -> None:
    value = packet.get(field)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{field} must be a non-empty string")


def _validate_false(packet: dict[str, Any], field: str, errors: list[str]) -> None:
    if packet.get(field) is not False:
        errors.append(f"{field} must be false")


def build_mission_task_packet(
    *,
    mission_id: str,
    mission_workspace_path: str,
    activation_approval_packet_path: str,
    sender: str = "OpenClaw",
    recipient: str = "Codex",
    task_id: str | None = None,
    run_id: str | None = None,
    priority: str = "normal",
    created_at: str | None = None,
    updated_at: str | None = None,
    request: str | None = None,
    expected_output: str | None = None,
    allowed_write_paths: list[str] | None = None,
    artifacts: list[str] | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Build a validated mission dry-review packet preview.

    The returned packet is intentionally inert: ``agent_bus_task_written`` is
    always false and no storage backend is touched.
    """

    slug = _safe_slug(mission_id)
    now = created_at or _now_iso()
    packet = {
        "task_id": task_id or f"mission-task-preview-{slug}",
        "run_id": run_id or f"mission-run-preview-{slug}",
        "reply_to": None,
        "from": sender,
        "to": recipient,
        "intent": MISSION_INTENT,
        "status": MISSION_STATUS,
        "priority": priority,
        "owner": None,
        "request": request
        or (
            "Review the validated Mission Mode dry-run workspace and return only "
            "local proposal/risk/completion evidence. Do not activate the mission, "
            "enqueue further tasks, call providers, use a browser, or send externally."
        ),
        "expected_output": expected_output
        or (
            "A local mission dry-review result with proof references, blocker list, "
            "and explicit confirmation that no activation or external side effects occurred."
        ),
        "depends_on": [],
        "artifacts": artifacts
        if artifacts is not None
        else [mission_workspace_path, activation_approval_packet_path],
        "task_type": MISSION_TASK_TYPE,
        "mission_id": mission_id,
        "mission_workspace_path": mission_workspace_path,
        "activation_approval_packet_path": activation_approval_packet_path,
        "write_policy": MISSION_WRITE_POLICY,
        "forbidden_actions": list(MISSION_FORBIDDEN_ACTIONS),
        "allowed_result_shapes": list(MISSION_ALLOWED_RESULT_SHAPES),
        "execution_constraints": {
            "allow_shell_commands": False,
            "allow_live_subprocess": False,
            "write_policy": MISSION_WRITE_POLICY,
            "allowed_write_paths": allowed_write_paths or list(MISSION_ALLOWED_WRITE_ROOTS),
        },
        "agent_bus_task_written": False,
        "activation_performed": False,
        "workflow_evolution_applied": False,
        "notes": notes
        or "Mission task packet preview only; live bus enqueue requires separate approval.",
        "created_at": now,
        "updated_at": updated_at or now,
        "expires_at": None,
    }

    validation = validate_mission_task_packet(packet)
    if not validation["ok"]:
        raise ValueError("; ".join(validation["errors"]))
    return packet


def validate_mission_task_packet(packet: dict[str, Any]) -> dict[str, Any]:
    """Validate an inert Mission Mode Agent Bus packet preview."""

    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(packet, dict):
        return {"ok": False, "errors": ["packet must be an object"], "warnings": []}

    for field in REQUIRED_BASE_FIELDS + REQUIRED_MISSION_FIELDS:
        if field not in packet:
            errors.append(f"missing required field: {field}")

    for field in REQUIRED_BASE_FIELDS:
        if field in packet:
            _validate_non_empty_string(packet, field, errors)

    for field in (
        "task_type",
        "mission_id",
        "mission_workspace_path",
        "activation_approval_packet_path",
        "write_policy",
    ):
        if field in packet:
            _validate_non_empty_string(packet, field, errors)

    if packet.get("intent") != MISSION_INTENT:
        errors.append(f"intent must be {MISSION_INTENT}")
    if packet.get("status") != MISSION_STATUS:
        errors.append(f"status must be {MISSION_STATUS}")
    if packet.get("task_type") != MISSION_TASK_TYPE:
        errors.append(f"task_type must be {MISSION_TASK_TYPE}")
    if packet.get("write_policy") != MISSION_WRITE_POLICY:
        errors.append(f"write_policy must be {MISSION_WRITE_POLICY}")

    if packet.get("from") not in MISSION_TASK_SENDERS:
        errors.append(f"from must be one of {list(MISSION_TASK_SENDERS)}")
    if packet.get("to") not in ADDRESSABLE_MISSION_RUNTIMES:
        errors.append(f"to must be one of {list(ADDRESSABLE_MISSION_RUNTIMES)}")
    if packet.get("priority", "normal") not in _ALLOWED_PRIORITIES:
        errors.append(f"priority must be one of {sorted(_ALLOWED_PRIORITIES)}")

    mission_workspace_path = _normalize_vault_relative_path(
        packet.get("mission_workspace_path"),
        "mission_workspace_path",
        errors,
    )
    activation_packet_path = _normalize_vault_relative_path(
        packet.get("activation_approval_packet_path"),
        "activation_approval_packet_path",
        errors,
    )
    if mission_workspace_path and not _path_within_allowed_roots(
        mission_workspace_path,
        ("07_LOGS/VentureOps-Missions/",),
    ):
        errors.append("mission_workspace_path must stay under 07_LOGS/VentureOps-Missions/")
    if activation_packet_path and not _path_within_allowed_roots(
        activation_packet_path,
        ("07_LOGS/VentureOps-Missions/",),
    ):
        errors.append("activation_approval_packet_path must stay under 07_LOGS/VentureOps-Missions/")

    constraints = packet.get("execution_constraints")
    if not isinstance(constraints, dict):
        errors.append("execution_constraints must be an object")
    else:
        if constraints.get("allow_shell_commands") is not False:
            errors.append("execution_constraints.allow_shell_commands must be false")
        if constraints.get("allow_live_subprocess") is not False:
            errors.append("execution_constraints.allow_live_subprocess must be false")
        if constraints.get("write_policy") != MISSION_WRITE_POLICY:
            errors.append(f"execution_constraints.write_policy must be {MISSION_WRITE_POLICY}")
        allowed_write_paths = constraints.get("allowed_write_paths")
        if not isinstance(allowed_write_paths, list) or not allowed_write_paths:
            errors.append("execution_constraints.allowed_write_paths must be a non-empty list")
        else:
            for index, item in enumerate(allowed_write_paths):
                if not isinstance(item, str) or not item.strip():
                    errors.append("execution_constraints.allowed_write_paths must contain non-empty strings")
                    continue
                normalized = _normalize_vault_relative_path(
                    item,
                    f"execution_constraints.allowed_write_paths[{index}]",
                    errors,
                )
                if normalized and normalized not in MISSION_ALLOWED_WRITE_ROOTS:
                    errors.append(
                        "execution_constraints.allowed_write_paths entries must match "
                        f"{list(MISSION_ALLOWED_WRITE_ROOTS)}"
                    )

    forbidden_actions = packet.get("forbidden_actions")
    if not isinstance(forbidden_actions, list):
        errors.append("forbidden_actions must be a list")
    else:
        missing = sorted(set(MISSION_FORBIDDEN_ACTIONS) - {str(item) for item in forbidden_actions})
        if missing:
            errors.append(f"forbidden_actions missing required entries: {missing}")

    allowed_result_shapes = packet.get("allowed_result_shapes")
    if not isinstance(allowed_result_shapes, list):
        errors.append("allowed_result_shapes must be a list")
    else:
        invalid = sorted(set(str(item) for item in allowed_result_shapes) - set(MISSION_ALLOWED_RESULT_SHAPES))
        if invalid:
            errors.append(f"allowed_result_shapes contains unsupported values: {invalid}")

    artifacts = packet.get("artifacts", [])
    if artifacts is not None and not isinstance(artifacts, list):
        errors.append("artifacts must be a list when present")
    if isinstance(artifacts, list) and len(artifacts) < 2:
        warnings.append("mission task packet should reference workspace and approval packet artifacts")

    _validate_false(packet, "agent_bus_task_written", errors)
    _validate_false(packet, "activation_performed", errors)
    _validate_false(packet, "workflow_evolution_applied", errors)

    return {"ok": not errors, "errors": errors, "warnings": warnings}


def vault_relative_path(path: str | Path, vault_root: str | Path) -> str:
    """Return a slash-normalized vault-relative path for packet fields."""

    root = Path(vault_root).resolve()
    candidate = Path(path)
    resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError(f"path escapes vault root: {path}") from exc
