"""Review-only approval packet preview for ChaseOS sub-agent activation."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from .activation import SubAgentActivationManager
from .models import SubAgentPreset, SubAgentValidationError
from .registry import SubAgentRegistry
from .router import SubAgentRuntimeRouter


SUBAGENT_APPROVAL_SCHEMA_VERSION = "subagent_activation_approval_packet.v1"
SUBAGENT_APPROVAL_PACKET_KIND = "subagent_activation_approval_preview"
SUBAGENT_APPROVAL_REQUEST_SCHEMA_VERSION = "subagent_activation_approval_request.v1"
SUBAGENT_APPROVAL_REQUEST_PACKET_KIND = "subagent_activation_approval_request"
SUBAGENT_APPROVAL_REQUEST_STATUS = "pending_operator_decision"
SUBAGENT_APPROVAL_REQUEST_ROOT = (
    Path("07_LOGS") / "Agent-Activity" / "_subagent_activation_approvals"
)
SUBAGENT_APPROVAL_CONSUMPTION_DRY_RUN_SCHEMA_VERSION = (
    "subagent_activation_approval_consumption_dry_run.v1"
)
SUBAGENT_APPROVAL_CONSUMPTION_DRY_RUN_PACKET_KIND = (
    "subagent_activation_approval_consumption_dry_run"
)
SUBAGENT_APPROVAL_CONSUMPTION_MARKER_ROOT = (
    SUBAGENT_APPROVAL_REQUEST_ROOT / "_consumption_markers"
)
SUBAGENT_APPROVAL_DECISION_SCHEMA_VERSION = (
    "subagent_activation_approval_decision.v1"
)
SUBAGENT_APPROVAL_DECISION_PACKET_KIND = (
    "subagent_activation_approval_decision"
)
SUBAGENT_APPROVAL_DECISION_ROOT = SUBAGENT_APPROVAL_REQUEST_ROOT / "_decisions"
SUBAGENT_APPROVAL_DECISION_BINDING_SCHEMA_VERSION = (
    "subagent_activation_approval_consumption_decision_binding.v1"
)
SUBAGENT_APPROVAL_DECISION_BINDING_PACKET_KIND = (
    "subagent_activation_approval_consumption_decision_binding"
)
SUBAGENT_APPROVAL_CONSUMPTION_MARKER_SCHEMA_VERSION = (
    "subagent_activation_approval_consumption_marker.v1"
)
SUBAGENT_APPROVAL_CONSUMPTION_MARKER_PACKET_KIND = (
    "subagent_activation_approval_consumption_marker"
)
SUBAGENT_AGENT_BUS_TASK_PACKET_PREVIEW_SCHEMA_VERSION = (
    "subagent_activation_agent_bus_task_packet_preview.v1"
)
SUBAGENT_AGENT_BUS_TASK_PACKET_PREVIEW_PACKET_KIND = (
    "subagent_activation_agent_bus_task_packet_preview"
)
SUBAGENT_AGENT_BUS_TASK_TYPE = "subagent.activation"
SUBAGENT_AGENT_BUS_TASK_ALLOWED_RESULT_SHAPES = (
    "proposal",
    "patch",
    "risk",
    "blocked",
    "complete",
)
SUBAGENT_APPROVAL_DECISIONS = {
    "approve": "approved",
    "approved": "approved",
    "deny": "denied",
    "denied": "denied",
}

BLOCKED_EFFECTS: tuple[str, ...] = (
    "approval_grant",
    "approval_consumption",
    "approval_artifact_persistence",
    "agent_bus_task_creation",
    "daemon_start",
    "runtime_dispatch",
    "provider_or_model_call",
    "browser_or_external_action",
    "governed_memory_persistence",
    "canonical_state_change",
)

DECISION_BLOCKED_EFFECTS: tuple[str, ...] = (
    "approval_consumption",
    "approval_consumption_marker_write",
    "agent_bus_task_creation",
    "daemon_start",
    "runtime_dispatch",
    "provider_or_model_call",
    "browser_or_external_action",
    "governed_memory_persistence",
    "canonical_state_change",
)

AGENT_BUS_TASK_PACKET_BLOCKED_EFFECTS: tuple[str, ...] = (
    "agent_bus_task_creation",
    "daemon_start",
    "runtime_dispatch",
    "provider_or_model_call",
    "browser_or_external_action",
    "governed_memory_persistence",
    "canonical_state_change",
)

AGENT_BUS_TASK_PACKET_FORBIDDEN_ACTIONS: tuple[str, ...] = (
    "agent_bus_enqueue",
    "daemon_start",
    "runtime_dispatch",
    "provider_or_model_call",
    "browser_or_external_action",
    "governed_memory_write",
    "canonical_writeback",
    "approval_request_mutation",
    "approval_decision_mutation",
)

FUTURE_APPROVAL_REQUIREMENTS: tuple[str, ...] = (
    "operator_approval_statement must explicitly approve this preset, mode, task id, and work fingerprint",
    "approval_id must be supplied by an approval artifact writer, not by this preview",
    "approval_artifact_path must stay inside the governed ChaseOS approval/log tree",
    "work_fingerprint must match the preview exactly immediately before enqueue or dispatch",
    "runtime route must be recomputed and match the approved selected bus name before use",
    "Agent Bus enqueue, daemon start, runtime dispatch, and provider/browser actions require separate executor code",
)


def _json_ready(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    return value


def _stable_digest(payload: dict[str, Any]) -> str:
    material = json.dumps(_json_ready(payload), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _safe_relative(path: Path, vault_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(vault_root.resolve())).replace("\\", "/")
    except (OSError, ValueError):
        return str(path).replace("\\", "/")


def _resolve_inside_vault(path: str | Path, vault_root: Path) -> tuple[Path, bool]:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = vault_root / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(vault_root.resolve())
        return resolved, True
    except ValueError:
        return resolved, False


def _route_dict(route) -> dict[str, Any]:
    return {
        "route_status": route.route_status,
        "selected_runtime": route.selected_runtime,
        "selected_bus_name": route.selected_bus_name,
        "fallback_runtimes": list(route.fallback_runtimes),
        "unavailable_preferences": list(route.unavailable_preferences),
        "blocked_reasons": list(route.blocked_reasons),
    }


def _preset_approval_summary(preset: SubAgentPreset) -> dict[str, Any]:
    return {
        "id": preset.id,
        "name": preset.name,
        "role": preset.role,
        "version": preset.version,
        "modes": list(preset.modes),
        "runtime_preferences": list(preset.runtime_preferences),
        "source_path": preset.source_path,
        "tool_policy": preset.tools.__dict__,
        "memory_policy": preset.memory.__dict__,
        "compute_budget": preset.compute.__dict__,
        "lifecycle_policy": preset.lifecycle.__dict__,
        "output_contract": preset.output.__dict__,
    }


def _authority_flags() -> dict[str, bool]:
    return {
        "read_only": True,
        "preview_only": True,
        "writes_performed": False,
        "approval_artifact_write_allowed": False,
        "approval_consumption_allowed": False,
        "approval_granted": False,
        "agent_bus_enqueue_allowed": False,
        "daemon_start_allowed": False,
        "runtime_dispatch_allowed": False,
        "provider_call_allowed": False,
        "browser_launch_allowed": False,
        "external_action_allowed": False,
        "governed_memory_write_allowed": False,
        "canonical_writeback_allowed": False,
    }


def _approval_request_authority_flags(*, artifact_written: bool = False) -> dict[str, bool]:
    return {
        "read_only": not artifact_written,
        "preview_only": not artifact_written,
        "writes_performed": artifact_written,
        "approval_artifact_write_allowed": artifact_written,
        "approval_request_written": artifact_written,
        "approval_consumption_allowed": False,
        "approval_granted": False,
        "agent_bus_enqueue_allowed": False,
        "daemon_start_allowed": False,
        "runtime_dispatch_allowed": False,
        "provider_call_allowed": False,
        "browser_launch_allowed": False,
        "external_action_allowed": False,
        "governed_memory_write_allowed": False,
        "canonical_writeback_allowed": False,
    }


def _approval_consumption_dry_run_authority_flags() -> dict[str, bool]:
    return {
        "read_only": True,
        "preview_only": True,
        "writes_performed": False,
        "approval_artifact_write_allowed": False,
        "approval_request_written": False,
        "approval_consumption_allowed": False,
        "approval_consumption_marker_write_allowed": False,
        "approval_granted": False,
        "agent_bus_enqueue_allowed": False,
        "daemon_start_allowed": False,
        "runtime_dispatch_allowed": False,
        "provider_call_allowed": False,
        "browser_launch_allowed": False,
        "external_action_allowed": False,
        "governed_memory_write_allowed": False,
        "canonical_writeback_allowed": False,
    }


def _approval_decision_authority_flags(*, decision_written: bool = False) -> dict[str, bool]:
    return {
        "read_only": not decision_written,
        "preview_only": not decision_written,
        "writes_performed": decision_written,
        "approval_artifact_write_allowed": False,
        "approval_request_written": False,
        "approval_decision_write_allowed": decision_written,
        "approval_decision_written": decision_written,
        "approval_consumption_allowed": False,
        "approval_consumption_marker_write_allowed": False,
        "approval_granted": False,
        "agent_bus_enqueue_allowed": False,
        "daemon_start_allowed": False,
        "runtime_dispatch_allowed": False,
        "provider_call_allowed": False,
        "browser_launch_allowed": False,
        "external_action_allowed": False,
        "governed_memory_write_allowed": False,
        "canonical_writeback_allowed": False,
    }


def _approval_decision_binding_authority_flags() -> dict[str, bool]:
    flags = _approval_consumption_dry_run_authority_flags()
    flags.update(
        {
            "approval_decision_write_allowed": False,
            "approval_decision_written": False,
            "approval_decision_binding_read_allowed": True,
        }
    )
    return flags


def _approval_consumption_marker_authority_flags(*, marker_written: bool = False) -> dict[str, bool]:
    return {
        "read_only": not marker_written,
        "preview_only": not marker_written,
        "writes_performed": marker_written,
        "approval_artifact_write_allowed": False,
        "approval_request_written": False,
        "approval_decision_write_allowed": False,
        "approval_decision_written": False,
        "approval_decision_binding_read_allowed": True,
        "approval_consumption_allowed": False,
        "approval_consumption_marker_write_allowed": marker_written,
        "approval_consumption_marker_written": marker_written,
        "approval_granted": False,
        "agent_bus_enqueue_allowed": False,
        "daemon_start_allowed": False,
        "runtime_dispatch_allowed": False,
        "provider_call_allowed": False,
        "browser_launch_allowed": False,
        "external_action_allowed": False,
        "governed_memory_write_allowed": False,
        "canonical_writeback_allowed": False,
    }


def _agent_bus_task_packet_preview_authority_flags() -> dict[str, bool]:
    return {
        "read_only": True,
        "preview_only": True,
        "writes_performed": False,
        "approval_artifact_write_allowed": False,
        "approval_request_written": False,
        "approval_decision_write_allowed": False,
        "approval_decision_written": False,
        "approval_decision_binding_read_allowed": True,
        "approval_consumption_allowed": False,
        "approval_consumption_marker_write_allowed": False,
        "approval_consumption_marker_written": False,
        "agent_bus_task_preview_allowed": True,
        "agent_bus_enqueue_allowed": False,
        "agent_bus_task_written": False,
        "daemon_start_allowed": False,
        "runtime_dispatch_allowed": False,
        "provider_call_allowed": False,
        "browser_launch_allowed": False,
        "external_action_allowed": False,
        "governed_memory_write_allowed": False,
        "canonical_writeback_allowed": False,
    }


def _read_json_object(
    path: Path,
    *,
    artifact_label: str = "approval_artifact",
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return None, f"{artifact_label}_read_failed: {exc}"
    except json.JSONDecodeError as exc:
        return None, f"{artifact_label}_json_invalid: {exc}"
    if not isinstance(payload, dict):
        return None, f"{artifact_label}_json_not_object"
    return payload, None


def _packet_id_path_safe(approval_packet_id: str) -> bool:
    if not approval_packet_id:
        return False
    return (
        Path(approval_packet_id).name == approval_packet_id
        and "/" not in approval_packet_id
        and "\\" not in approval_packet_id
        and ".." not in approval_packet_id
    )


def _known_agent_bus_names(vault_root: Path) -> set[str]:
    try:
        from runtime.agent_bus.bus import get_known_runtimes

        return set(get_known_runtimes(vault_root))
    except Exception:
        return {"Archon", "Codex", "Hermes", "OpenClaw"}


def _validate_agent_bus_task_packet_preview(packet: Mapping[str, Any]) -> list[str]:
    required = (
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
    errors: list[str] = []
    for field in required:
        value = packet.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"agent_bus_task_packet_missing_or_empty_{field}")
    if packet.get("intent") != "TASK":
        errors.append("agent_bus_task_packet_intent_must_be_TASK")
    if packet.get("status") != "open":
        errors.append("agent_bus_task_packet_status_must_be_open")
    if packet.get("priority") not in {"low", "normal", "high", "critical"}:
        errors.append("agent_bus_task_packet_priority_invalid")
    constraints = packet.get("execution_constraints")
    if not isinstance(constraints, dict):
        errors.append("agent_bus_task_packet_execution_constraints_missing")
    else:
        if constraints.get("allow_shell_commands") is not False:
            errors.append("agent_bus_task_packet_shell_commands_must_be_false")
        if constraints.get("allow_live_subprocess") is not False:
            errors.append("agent_bus_task_packet_live_subprocess_must_be_false")
        if constraints.get("write_policy") != "none":
            errors.append("agent_bus_task_packet_write_policy_must_be_none")
        if constraints.get("allowed_write_paths") != []:
            errors.append("agent_bus_task_packet_allowed_write_paths_must_be_empty")
    return errors


def _approval_material(
    *,
    preset: SubAgentPreset,
    route: dict[str, Any],
    mode: str | None,
    task_id: str,
    objective: str,
    requested_by: str,
) -> dict[str, Any]:
    return {
        "schema_version": SUBAGENT_APPROVAL_SCHEMA_VERSION,
        "packet_kind": SUBAGENT_APPROVAL_PACKET_KIND,
        "requested_action": "subagent.activation_or_agent_bus_enqueue",
        "preset": _preset_approval_summary(preset),
        "route": route,
        "task_scope": {
            "mode": mode or "",
            "task_id": task_id,
            "objective": objective,
            "requested_by": requested_by,
        },
        "blocked_effects": list(BLOCKED_EFFECTS),
        "future_approval_requirements": list(FUTURE_APPROVAL_REQUIREMENTS),
    }


def _approval_request_text(*, approval_packet_id: str, work_fingerprint: str, payload: dict[str, Any]) -> str:
    preset = payload.get("preset") or {}
    task_scope = payload.get("task_scope") or {}
    route = payload.get("route") or {}
    return "\n".join(
        [
            "APPROVE SUB-AGENT ACTIVATION OR AGENT BUS ENQUEUE ONLY:",
            f"- approval_packet_id: {approval_packet_id}",
            f"- preset_id: {preset.get('id') or ''}",
            f"- mode: {task_scope.get('mode') or ''}",
            f"- task_id: {task_scope.get('task_id') or ''}",
            f"- work_fingerprint: {work_fingerprint}",
            f"- selected_bus_name: {route.get('selected_bus_name') or ''}",
            "",
            "No approval consumption.",
            "No Agent Bus task creation.",
            "No daemon start.",
            "No runtime dispatch.",
            "No provider/model call.",
            "No browser or external action.",
            "No governed memory write.",
            "No canonical state change.",
        ]
    )


def _approval_artifact_payload(
    *,
    approval_packet_id: str,
    requested_by: str,
    vault_root: Path,
    approval_preview: dict[str, Any],
    approval_artifact_path: str,
) -> dict[str, Any]:
    work_fingerprint = str(approval_preview.get("work_fingerprint") or "")
    return {
        "schema_version": SUBAGENT_APPROVAL_REQUEST_SCHEMA_VERSION,
        "packet_kind": SUBAGENT_APPROVAL_REQUEST_PACKET_KIND,
        "approval_packet_id": approval_packet_id,
        "status": SUBAGENT_APPROVAL_REQUEST_STATUS,
        "created_at": datetime.now(UTC).isoformat(),
        "requested_by": requested_by,
        "vault_root": str(vault_root),
        "approval_artifact_path": approval_artifact_path,
        "requested_action": "subagent.activation_or_agent_bus_enqueue",
        "work_fingerprint": work_fingerprint,
        "approval_scope": {
            "preset_id": (approval_preview.get("preset") or {}).get("id"),
            "mode": (approval_preview.get("task_scope") or {}).get("mode"),
            "task_id": (approval_preview.get("task_scope") or {}).get("task_id"),
            "objective": (approval_preview.get("task_scope") or {}).get("objective"),
            "selected_runtime": (approval_preview.get("route") or {}).get("selected_runtime"),
            "selected_bus_name": (approval_preview.get("route") or {}).get("selected_bus_name"),
            "approval_request_only": True,
            "approval_consumption_allowed": False,
            "agent_bus_task_allowed": False,
            "daemon_start_allowed": False,
            "runtime_dispatch_allowed": False,
            "provider_or_model_call_allowed": False,
            "browser_or_external_action_allowed": False,
            "governed_memory_write_allowed": False,
            "canonical_writeback_allowed": False,
        },
        "operator_confirmation_text": _approval_request_text(
            approval_packet_id=approval_packet_id,
            work_fingerprint=work_fingerprint,
            payload=approval_preview,
        ),
        "approval_packet_preview": approval_preview.get("approval_packet_preview"),
        "activation_context_preview": approval_preview.get("activation_context_preview"),
        "future_writer_requirements": [
            "approval artifact writer must require the exact work_fingerprint before writing",
            "approval artifact writer must write only inside the governed ChaseOS approval/log tree",
            "approval artifact writer must refuse to overwrite an existing approval artifact",
            "approval artifact writer must leave approval_granted and approval_consumed false",
        ],
        "future_executor_requirements": list(FUTURE_APPROVAL_REQUIREMENTS),
        "blocked_effects": list(BLOCKED_EFFECTS),
        "authority_flags": _approval_request_authority_flags(artifact_written=False),
        "approval_request_only": True,
        "approval_granted": False,
        "approval_consumed": False,
        "agent_bus_task_written": False,
        "daemon_started": False,
        "runtime_dispatched": False,
        "provider_or_model_call_performed": False,
        "browser_or_external_action_performed": False,
        "governed_memory_write_performed": False,
        "canonical_writeback_performed": False,
    }


def _approval_decision_text(
    *,
    decision: str,
    approval_packet_id: str,
    work_fingerprint: str,
    scope: Mapping[str, Any],
) -> str:
    verb = "APPROVED" if decision == "approved" else "DENIED"
    return "\n".join(
        [
            f"{verb} SUB-AGENT ACTIVATION OR AGENT BUS ENQUEUE DECISION ONLY:",
            f"- approval_packet_id: {approval_packet_id}",
            f"- preset_id: {scope.get('preset_id') or ''}",
            f"- mode: {scope.get('mode') or ''}",
            f"- task_id: {scope.get('task_id') or ''}",
            f"- work_fingerprint: {work_fingerprint}",
            f"- selected_bus_name: {scope.get('selected_bus_name') or ''}",
            "",
            "Decision artifact only.",
            "No approval consumption.",
            "No approval consumption marker write.",
            "No Agent Bus task creation.",
            "No daemon start.",
            "No runtime dispatch.",
            "No provider/model call.",
            "No browser or external action.",
            "No governed memory write.",
            "No canonical state change.",
        ]
    )


def _approval_decision_payload(
    *,
    approval_packet_id: str,
    approval_artifact_path: str,
    decision_artifact_path: str,
    decision: str,
    reviewer_id: str,
    requested_by: str,
    reason: str,
    vault_root: Path,
    approval_payload: Mapping[str, Any],
    current_preview: Mapping[str, Any] | None,
    decision_written: bool,
) -> dict[str, Any]:
    scope = approval_payload.get("approval_scope")
    approval_scope = dict(scope) if isinstance(scope, dict) else {}
    work_fingerprint = str(approval_payload.get("work_fingerprint") or "")
    current_route = (current_preview or {}).get("route") if current_preview else {}
    record = {
        "schema_version": SUBAGENT_APPROVAL_DECISION_SCHEMA_VERSION,
        "packet_kind": SUBAGENT_APPROVAL_DECISION_PACKET_KIND,
        "approval_packet_id": approval_packet_id,
        "status": "approval_decision_recorded" if decision_written else "approval_decision_preview",
        "decision_status": "recorded" if decision_written else "preview",
        "created_at": datetime.now(UTC).isoformat(),
        "reviewer_id": reviewer_id,
        "requested_by": requested_by,
        "operator_decision": decision,
        "decision": decision,
        "reason": reason,
        "vault_root": str(vault_root),
        "source_approval_artifact_path": approval_artifact_path,
        "decision_artifact_path": decision_artifact_path,
        "source_approval_status": approval_payload.get("status"),
        "source_approval_packet_kind": approval_payload.get("packet_kind"),
        "source_approval_schema_version": approval_payload.get("schema_version"),
        "work_fingerprint": work_fingerprint,
        "approval_scope": approval_scope,
        "current_work_fingerprint": (current_preview or {}).get("work_fingerprint"),
        "current_approval_packet_id": (current_preview or {}).get("approval_packet_id"),
        "current_selected_runtime": current_route.get("selected_runtime") if isinstance(current_route, dict) else "",
        "current_selected_bus_name": current_route.get("selected_bus_name") if isinstance(current_route, dict) else "",
        "operator_confirmation_text": _approval_decision_text(
            decision=decision,
            approval_packet_id=approval_packet_id,
            work_fingerprint=work_fingerprint,
            scope=approval_scope,
        ),
        "decision_effect": (
            "records_operator_approval_for_one_future_subagent_activation_or_agent_bus_enqueue_only"
            if decision == "approved"
            else "records_operator_denial_and_blocks_future_subagent_activation_or_agent_bus_enqueue"
        ),
        "immutable": True,
        "append_only": True,
        "approval_decision_only": True,
        "approval_decision_written": decision_written,
        "approval_request_mutated": False,
        "approval_consumption_allowed": False,
        "approval_consumption_required_for_future_executor": decision == "approved",
        "approval_consumed": False,
        "decision_consumed": False,
        "approval_consumption_marker_written": False,
        "agent_bus_task_written": False,
        "daemon_started": False,
        "runtime_dispatched": False,
        "provider_or_model_call_performed": False,
        "browser_or_external_action_performed": False,
        "governed_memory_write_performed": False,
        "canonical_writeback_performed": False,
        "blocked_effects": list(DECISION_BLOCKED_EFFECTS),
        "authority_flags": _approval_decision_authority_flags(decision_written=decision_written),
        "next_recommended_pass": (
            "sub-agent-presets-approval-consumption-decision-binding"
            if decision == "approved"
            else "sub-agent-presets-denied-approval-closeout"
        ),
    }
    record["decision_digest_sha256"] = _stable_digest({**record, "decision_digest_sha256": ""})
    return record


def build_subagent_approval_packet_preview(
    preset_id: str,
    *,
    mode: str | None = None,
    task_id: str = "subagent-approval-preview",
    objective: str = "Preview sub-agent activation approval packet without approval or dispatch.",
    requested_by: str = "operator",
    vault_root: str | Path | None = None,
    availability: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a deterministic approval packet preview without writing or executing."""

    root = Path(vault_root) if vault_root is not None else Path.cwd()
    registry = SubAgentRegistry(vault_root=root)
    try:
        preset = registry.get_preset(preset_id)
    except KeyError as exc:
        return {
            "ok": False,
            "status": "unknown_preset",
            "command": "subagents.approval-preview",
            "schema_version": SUBAGENT_APPROVAL_SCHEMA_VERSION,
            "read_only": True,
            "preview_only": True,
            "writes_performed": False,
            "preset_id": preset_id,
            "errors": [str(exc)],
            "blockers": ["unknown_preset"],
            "authority_flags": _authority_flags(),
            "blocked_effects": list(BLOCKED_EFFECTS),
        }

    router = SubAgentRuntimeRouter(vault_root=root, availability=availability)
    route = router.select_runtime(preset)
    route_payload = _route_dict(route)
    blockers: list[str] = []
    errors: list[str] = []
    activation_preview = None

    if not mode:
        blockers.append("mode_required_for_activation_approval")
    if not route.is_routable:
        blockers.extend(route.blocked_reasons)

    if mode:
        try:
            context = SubAgentActivationManager(vault_root=root, router=router).build_activation_context(
                preset,
                task_id=task_id,
                objective=objective,
                mode=mode,
                input_payload={"cli": "approval-preview"},
                activation_reason="cli approval-preview",
            )
        except SubAgentValidationError as exc:
            errors.append(str(exc))
            blockers.append("mode_invalid")
        else:
            activation_preview = context.to_dict()
            if context.state != "activated":
                blockers.extend(context.blocked_reasons)

    material = _approval_material(
        preset=preset,
        route=route_payload,
        mode=mode,
        task_id=task_id,
        objective=objective,
        requested_by=requested_by,
    )
    work_fingerprint = _stable_digest(material)
    approval_packet_id = f"subagent-activation-appr-{work_fingerprint[:16]}"
    approval_artifact_path_preview = (
        f"07_LOGS/Agent-Activity/_subagent_activation_approvals/{approval_packet_id}.json"
    )
    ready_for_operator_decision = not blockers

    approval_packet_preview = {
        **material,
        "approval_packet_id": approval_packet_id,
        "approval_status": "preview_only",
        "work_fingerprint": work_fingerprint,
        "approval_artifact_path_preview": approval_artifact_path_preview,
        "ready_for_operator_decision": ready_for_operator_decision,
        "approval_request_created": False,
        "approval_artifact_written": False,
        "approval_granted": False,
        "approval_consumed": False,
        "agent_bus_task_written": False,
        "runtime_dispatched": False,
        "authority_flags": _authority_flags(),
    }

    return {
        "ok": ready_for_operator_decision,
        "status": "ready_for_operator_decision" if ready_for_operator_decision else "blocked",
        "command": "subagents.approval-preview",
        "schema_version": SUBAGENT_APPROVAL_SCHEMA_VERSION,
        "packet_kind": SUBAGENT_APPROVAL_PACKET_KIND,
        "read_only": True,
        "preview_only": True,
        "writes_performed": False,
        "vault_root": str(root),
        "requested_by": requested_by,
        "approval_required_for": [
            "future_agent_bus_enqueue",
            "future_daemon_start",
            "future_runtime_dispatch",
        ],
        "approval_status": "preview_only",
        "approval_packet_id": approval_packet_id,
        "work_fingerprint": work_fingerprint,
        "approval_artifact_path_preview": approval_artifact_path_preview,
        "approval_request_created": False,
        "approval_artifact_written": False,
        "approval_granted": False,
        "approval_consumed": False,
        "agent_bus_task_written": False,
        "daemon_started": False,
        "runtime_dispatched": False,
        "provider_or_model_call_performed": False,
        "browser_or_external_action_performed": False,
        "governed_memory_write_performed": False,
        "canonical_writeback_performed": False,
        "ready_for_operator_decision": ready_for_operator_decision,
        "preset": _preset_approval_summary(preset),
        "route": route_payload,
        "task_scope": {
            "mode": mode or "",
            "task_id": task_id,
            "objective": objective,
            "requested_by": requested_by,
        },
        "activation_context_preview": activation_preview,
        "approval_packet_preview": approval_packet_preview,
        "future_approval_requirements": list(FUTURE_APPROVAL_REQUIREMENTS),
        "blocked_effects": list(BLOCKED_EFFECTS),
        "blockers": sorted(set(blockers)),
        "errors": errors,
        "authority_flags": _authority_flags(),
        "next_recommended_pass": (
            "sub-agent-presets-approval-artifact-writer"
            if ready_for_operator_decision
            else "sub-agent-presets-approval-preview-repair"
        ),
    }


def build_subagent_approval_consumption_dry_run(
    approval_artifact_path: str | Path,
    *,
    expected_work_fingerprint: str | None = None,
    vault_root: str | Path | None = None,
    availability: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Validate a pending approval request artifact without consuming it."""

    root = Path(vault_root) if vault_root is not None else Path.cwd()
    approval_abs_path, approval_path_inside = _resolve_inside_vault(approval_artifact_path, root)
    approval_rel_path = _safe_relative(approval_abs_path, root)
    expected = (expected_work_fingerprint or "").strip()
    blockers: list[str] = []
    errors: list[str] = []

    approval_payload: dict[str, Any] | None = None
    read_error: str | None = None
    if not approval_path_inside:
        blockers.append("approval_artifact_path_outside_vault")
    elif not approval_abs_path.is_file():
        blockers.append("approval_artifact_missing")
    else:
        approval_payload, read_error = _read_json_object(approval_abs_path)
        if read_error:
            blockers.append(read_error.split(":", 1)[0])
            errors.append(read_error)

    payload = approval_payload or {}
    scope = payload.get("approval_scope") if isinstance(payload.get("approval_scope"), dict) else {}
    authority = payload.get("authority_flags") if isinstance(payload.get("authority_flags"), dict) else {}
    approval_packet_id = str(payload.get("approval_packet_id") or "")
    artifact_work_fingerprint = str(payload.get("work_fingerprint") or "")
    artifact_status = str(payload.get("status") or "")
    requested_by = str(payload.get("requested_by") or "operator")
    preset_id = str(scope.get("preset_id") or "")
    mode = str(scope.get("mode") or "")
    task_id = str(scope.get("task_id") or "")
    objective = str(scope.get("objective") or "")
    selected_runtime = str(scope.get("selected_runtime") or "")
    selected_bus_name = str(scope.get("selected_bus_name") or "")

    if approval_payload is not None:
        if payload.get("schema_version") != SUBAGENT_APPROVAL_REQUEST_SCHEMA_VERSION:
            blockers.append("approval_request_schema_invalid")
        if payload.get("packet_kind") != SUBAGENT_APPROVAL_REQUEST_PACKET_KIND:
            blockers.append("approval_request_packet_kind_invalid")
        if artifact_status == SUBAGENT_APPROVAL_REQUEST_STATUS:
            blockers.append("operator_approval_decision_required")
        else:
            blockers.append("unsupported_approval_status_until_decision_schema_exists")
        if not approval_packet_id:
            blockers.append("approval_packet_id_missing")
        elif not _packet_id_path_safe(approval_packet_id):
            blockers.append("approval_packet_id_path_unsafe")
        if not artifact_work_fingerprint:
            blockers.append("work_fingerprint_missing")
        if expected and expected != artifact_work_fingerprint:
            blockers.append("expected_work_fingerprint_mismatch")
        if not all([preset_id, mode, task_id, objective, selected_bus_name]):
            blockers.append("approval_scope_incomplete")
        if payload.get("approval_request_only") is not True:
            blockers.append("approval_artifact_not_request_only")
        if payload.get("approval_granted") is not False:
            blockers.append("approval_artifact_must_not_grant_approval")
        if payload.get("approval_consumed") is not False:
            blockers.append("approval_artifact_already_consumed_or_ambiguous")
        if payload.get("agent_bus_task_written") is not False:
            blockers.append("approval_artifact_agent_bus_task_state_invalid")
        if payload.get("daemon_started") is not False or payload.get("runtime_dispatched") is not False:
            blockers.append("approval_artifact_execution_state_invalid")
        if authority.get("approval_consumption_allowed") is not False:
            blockers.append("approval_authority_does_not_block_consumption")
        if authority.get("agent_bus_enqueue_allowed") is not False:
            blockers.append("approval_authority_does_not_block_agent_bus_enqueue")
        if authority.get("runtime_dispatch_allowed") is not False:
            blockers.append("approval_authority_does_not_block_runtime_dispatch")

    current_preview: dict[str, Any] | None = None
    current_work_fingerprint = ""
    current_approval_packet_id = ""
    current_selected_runtime = ""
    current_selected_bus_name = ""
    if approval_payload is not None and all([preset_id, mode, task_id, objective]):
        current_preview = build_subagent_approval_packet_preview(
            preset_id,
            mode=mode,
            task_id=task_id,
            objective=objective,
            requested_by=requested_by,
            vault_root=root,
            availability=availability,
        )
        current_work_fingerprint = str(current_preview.get("work_fingerprint") or "")
        current_approval_packet_id = str(current_preview.get("approval_packet_id") or "")
        current_route = current_preview.get("route") or {}
        current_selected_runtime = str(current_route.get("selected_runtime") or "")
        current_selected_bus_name = str(current_route.get("selected_bus_name") or "")
        if not current_preview.get("ok"):
            blockers.append("current_approval_preview_not_ready")
        if current_work_fingerprint != artifact_work_fingerprint:
            blockers.append("current_work_fingerprint_mismatch")
        if current_approval_packet_id != approval_packet_id:
            blockers.append("current_approval_packet_id_mismatch")
        if current_selected_runtime != selected_runtime or current_selected_bus_name != selected_bus_name:
            blockers.append("current_route_mismatch")

    marker_rel_path: Path | None = None
    marker_abs_path: Path | None = None
    marker_path_inside = False
    marker_exists = False
    if approval_packet_id and _packet_id_path_safe(approval_packet_id):
        marker_rel_path = SUBAGENT_APPROVAL_CONSUMPTION_MARKER_ROOT / f"{approval_packet_id}.json"
        marker_abs_path, marker_path_inside = _resolve_inside_vault(marker_rel_path, root)
        marker_exists = marker_abs_path.exists()
        if not marker_path_inside:
            blockers.append("approval_consumption_marker_path_outside_vault")
        if marker_exists:
            blockers.append("approval_consumption_marker_already_exists")

    blockers = sorted(set(blockers))
    decision_blockers = {"operator_approval_decision_required"}
    non_decision_blockers = [blocker for blocker in blockers if blocker not in decision_blockers]
    dry_run_valid = approval_payload is not None and not non_decision_blockers
    status = (
        "blocked_pending_operator_decision"
        if dry_run_valid and "operator_approval_decision_required" in blockers
        else "blocked"
        if blockers
        else "approval_consumption_dry_run_ready_no_execution"
    )

    checks = {
        "approval_artifact_path_in_vault": approval_path_inside,
        "approval_artifact_present": approval_abs_path.is_file() if approval_path_inside else False,
        "approval_artifact_json_valid": approval_payload is not None,
        "approval_schema_valid": payload.get("schema_version") == SUBAGENT_APPROVAL_REQUEST_SCHEMA_VERSION,
        "approval_packet_kind_valid": payload.get("packet_kind") == SUBAGENT_APPROVAL_REQUEST_PACKET_KIND,
        "approval_status_pending": artifact_status == SUBAGENT_APPROVAL_REQUEST_STATUS,
        "approval_request_only": payload.get("approval_request_only") is True,
        "approval_not_granted": payload.get("approval_granted") is False,
        "approval_not_consumed": payload.get("approval_consumed") is False,
        "agent_bus_task_not_written": payload.get("agent_bus_task_written") is False,
        "runtime_not_dispatched": payload.get("runtime_dispatched") is False,
        "work_fingerprint_present": bool(artifact_work_fingerprint),
        "expected_work_fingerprint_matches": (expected == artifact_work_fingerprint) if expected else None,
        "current_preview_recomputed": current_preview is not None,
        "current_preview_ready": bool(current_preview and current_preview.get("ok")),
        "current_work_fingerprint_matches_artifact": bool(
            current_work_fingerprint and current_work_fingerprint == artifact_work_fingerprint
        ),
        "current_approval_packet_id_matches_artifact": bool(
            current_approval_packet_id and current_approval_packet_id == approval_packet_id
        ),
        "current_route_matches_artifact": bool(
            current_preview
            and
            current_selected_runtime == selected_runtime and current_selected_bus_name == selected_bus_name
        ),
        "future_consumption_marker_path_in_vault": marker_path_inside,
        "future_consumption_marker_absent": bool(marker_abs_path and not marker_exists),
        "no_writes_performed": True,
        "approval_consumption_blocked": True,
        "agent_bus_enqueue_blocked": True,
        "runtime_dispatch_blocked": True,
    }

    authority_flags = _approval_consumption_dry_run_authority_flags()
    return {
        "ok": dry_run_valid,
        "status": status,
        "command": "subagents.approval-consumption-dry-run",
        "schema_version": SUBAGENT_APPROVAL_CONSUMPTION_DRY_RUN_SCHEMA_VERSION,
        "packet_kind": SUBAGENT_APPROVAL_CONSUMPTION_DRY_RUN_PACKET_KIND,
        "read_only": True,
        "preview_only": True,
        "writes_performed": False,
        "vault_root": str(root),
        "approval_artifact_path": approval_rel_path,
        "approval_artifact_loaded": approval_payload is not None,
        "approval_packet_id": approval_packet_id,
        "approval_status": artifact_status,
        "approval_decision_status": artifact_status,
        "work_fingerprint": artifact_work_fingerprint,
        "expected_work_fingerprint": expected,
        "work_fingerprint_matched": (expected == artifact_work_fingerprint) if expected else None,
        "current_work_fingerprint": current_work_fingerprint,
        "current_approval_packet_id": current_approval_packet_id,
        "current_selected_runtime": current_selected_runtime,
        "current_selected_bus_name": current_selected_bus_name,
        "approved_selected_runtime": selected_runtime,
        "approved_selected_bus_name": selected_bus_name,
        "future_consumption_marker_path": (
            _safe_relative(marker_abs_path, root) if marker_abs_path else ""
        ),
        "future_consumption_marker_exists": marker_exists,
        "approval_consumption_ready": False,
        "approval_consumption_ready_for_future_executor": False,
        "approval_consumption_marker_written": False,
        "approval_granted": False,
        "approval_consumed": False,
        "agent_bus_task_written": False,
        "daemon_started": False,
        "runtime_dispatched": False,
        "provider_or_model_call_performed": False,
        "browser_or_external_action_performed": False,
        "governed_memory_write_performed": False,
        "canonical_writeback_performed": False,
        "approval_scope": scope,
        "current_approval_preview": current_preview,
        "checks": checks,
        "blocked_effects": list(BLOCKED_EFFECTS),
        "blockers": blockers,
        "errors": errors,
        "authority_flags": authority_flags,
        "next_recommended_pass": (
            "sub-agent-presets-approval-review-decision-contract"
            if dry_run_valid
            else "sub-agent-presets-approval-consumption-dry-run-repair"
        ),
    }


def build_subagent_approval_review_decision(
    approval_artifact_path: str | Path,
    *,
    decision: str,
    reviewer_id: str = "operator",
    reason: str | None = None,
    expected_work_fingerprint: str | None = None,
    write_approval_decision: bool = False,
    vault_root: str | Path | None = None,
    availability: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Preview or write one immutable operator decision for a pending request."""

    root = Path(vault_root) if vault_root is not None else Path.cwd()
    approval_abs_path, approval_path_inside = _resolve_inside_vault(approval_artifact_path, root)
    approval_rel_path = _safe_relative(approval_abs_path, root)
    expected = (expected_work_fingerprint or "").strip()
    reviewer = (reviewer_id or "operator").strip() or "operator"
    normalized_input = (decision or "").strip().lower()
    normalized_decision = SUBAGENT_APPROVAL_DECISIONS.get(normalized_input, normalized_input)
    blockers: list[str] = []
    errors: list[str] = []

    if normalized_decision not in {"approved", "denied"}:
        blockers.append("approval_decision_invalid")

    dry_run = build_subagent_approval_consumption_dry_run(
        approval_artifact_path,
        expected_work_fingerprint=expected,
        vault_root=root,
        availability=availability,
    )
    if not dry_run.get("ok"):
        blockers.extend(str(item) for item in dry_run.get("blockers") or [])
        errors.extend(str(item) for item in dry_run.get("errors") or [])

    approval_payload: dict[str, Any] | None = None
    read_error: str | None = None
    if approval_path_inside and approval_abs_path.is_file():
        approval_payload, read_error = _read_json_object(approval_abs_path)
        if read_error:
            blockers.append(read_error.split(":", 1)[0])
            errors.append(read_error)

    payload = approval_payload or {}
    approval_packet_id = str(payload.get("approval_packet_id") or dry_run.get("approval_packet_id") or "")
    work_fingerprint = str(payload.get("work_fingerprint") or dry_run.get("work_fingerprint") or "")
    requested_by = str(payload.get("requested_by") or "operator")

    if write_approval_decision and not expected:
        blockers.append("expected_work_fingerprint_required_for_decision_write")
    if write_approval_decision and expected and expected != work_fingerprint:
        blockers.append("expected_work_fingerprint_mismatch")
    if not approval_path_inside:
        blockers.append("approval_artifact_path_outside_vault")
    if not approval_abs_path.is_file():
        blockers.append("approval_artifact_missing")
    if approval_payload is None:
        blockers.append("approval_artifact_not_loaded")
    if payload.get("status") != SUBAGENT_APPROVAL_REQUEST_STATUS:
        blockers.append("approval_request_not_pending_operator_decision")
    if not approval_packet_id:
        blockers.append("approval_packet_id_missing")
    elif not _packet_id_path_safe(approval_packet_id):
        blockers.append("approval_packet_id_path_unsafe")
    if not work_fingerprint:
        blockers.append("work_fingerprint_missing")

    decision_rel_path = (
        SUBAGENT_APPROVAL_DECISION_ROOT / f"{approval_packet_id or 'unknown-approval-packet'}.json"
    )
    decision_abs_path, decision_path_inside = _resolve_inside_vault(decision_rel_path, root)
    decision_artifact_path = _safe_relative(decision_abs_path, root)
    decision_exists = decision_abs_path.exists()
    if not decision_path_inside:
        blockers.append("approval_decision_artifact_path_outside_vault")
    if decision_exists:
        blockers.append("approval_decision_artifact_already_exists_no_overwrite")

    dry_run_blockers = set(str(item) for item in dry_run.get("blockers") or [])
    non_decision_dry_run_blockers = dry_run_blockers - {"operator_approval_decision_required"}
    if non_decision_dry_run_blockers:
        blockers.extend(sorted(non_decision_dry_run_blockers))
    if "operator_approval_decision_required" not in dry_run_blockers:
        blockers.append("approval_request_not_at_operator_decision_boundary")

    current_preview = dry_run.get("current_approval_preview")
    if not isinstance(current_preview, dict):
        current_preview = None

    blockers = sorted(set(blockers))
    decision_record_writable = (
        approval_payload is not None
        and normalized_decision in {"approved", "denied"}
        and not blockers
    )
    decision_payload = _approval_decision_payload(
        approval_packet_id=approval_packet_id,
        approval_artifact_path=approval_rel_path,
        decision_artifact_path=decision_artifact_path,
        decision=normalized_decision if normalized_decision in {"approved", "denied"} else normalized_input,
        reviewer_id=reviewer,
        requested_by=requested_by,
        reason=reason or (
            "Operator approved one future sub-agent activation or Agent Bus enqueue."
            if normalized_decision == "approved"
            else "Operator denied this sub-agent activation or Agent Bus enqueue request."
        ),
        vault_root=root,
        approval_payload=payload,
        current_preview=current_preview,
        decision_written=False,
    )

    decision_written = False
    if write_approval_decision and decision_record_writable:
        written_payload = _approval_decision_payload(
            approval_packet_id=approval_packet_id,
            approval_artifact_path=approval_rel_path,
            decision_artifact_path=decision_artifact_path,
            decision=normalized_decision,
            reviewer_id=reviewer,
            requested_by=requested_by,
            reason=str(decision_payload.get("reason") or ""),
            vault_root=root,
            approval_payload=payload,
            current_preview=current_preview,
            decision_written=True,
        )
        decision_abs_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with decision_abs_path.open("x", encoding="utf-8") as handle:
                handle.write(json.dumps(written_payload, indent=2, default=str) + "\n")
        except FileExistsError:
            blockers.append("approval_decision_artifact_already_exists_no_overwrite")
        else:
            decision_written = True
            decision_payload = written_payload

    if write_approval_decision and not decision_written and not blockers:
        blockers.append("approval_decision_not_written_unknown_reason")
    blockers = sorted(set(blockers))

    ok = decision_written if write_approval_decision else decision_record_writable
    status = (
        f"approval_decision_written_{normalized_decision}_no_execution"
        if decision_written
        else "blocked"
        if blockers
        else f"ready_to_write_approval_decision_{normalized_decision}_no_execution"
    )
    checks = {
        "approval_artifact_path_in_vault": approval_path_inside,
        "approval_artifact_present": approval_abs_path.is_file() if approval_path_inside else False,
        "approval_artifact_json_valid": approval_payload is not None,
        "approval_status_pending": payload.get("status") == SUBAGENT_APPROVAL_REQUEST_STATUS,
        "decision_valid": normalized_decision in {"approved", "denied"},
        "expected_work_fingerprint_required_for_write": bool(write_approval_decision),
        "expected_work_fingerprint_matches": (expected == work_fingerprint) if expected else None,
        "current_work_fingerprint_matches_artifact": (
            dry_run.get("checks") or {}
        ).get("current_work_fingerprint_matches_artifact"),
        "current_approval_packet_id_matches_artifact": (
            dry_run.get("checks") or {}
        ).get("current_approval_packet_id_matches_artifact"),
        "current_route_matches_artifact": (dry_run.get("checks") or {}).get("current_route_matches_artifact"),
        "future_consumption_marker_absent": (dry_run.get("checks") or {}).get(
            "future_consumption_marker_absent"
        ),
        "decision_artifact_path_in_vault": decision_path_inside,
        "decision_artifact_absent_before_write": not decision_exists,
        "decision_artifact_written": decision_written,
        "approval_request_mutated": False,
        "approval_consumption_blocked": True,
        "agent_bus_enqueue_blocked": True,
        "runtime_dispatch_blocked": True,
    }
    authority_flags = _approval_decision_authority_flags(decision_written=decision_written)
    return {
        "ok": ok,
        "status": status,
        "command": "subagents.approval-review-decision",
        "schema_version": SUBAGENT_APPROVAL_DECISION_SCHEMA_VERSION,
        "packet_kind": SUBAGENT_APPROVAL_DECISION_PACKET_KIND,
        "read_only": not decision_written,
        "preview_only": not decision_written,
        "writes_performed": decision_written,
        "vault_root": str(root),
        "approval_artifact_path": approval_rel_path,
        "approval_artifact_loaded": approval_payload is not None,
        "approval_packet_id": approval_packet_id,
        "approval_request_status": payload.get("status"),
        "operator_decision": normalized_decision,
        "decision": normalized_decision,
        "reviewer_id": reviewer,
        "reason": decision_payload.get("reason"),
        "write_approval_decision_requested": write_approval_decision,
        "approval_decision_written": decision_written,
        "approval_decision_recorded": decision_written,
        "decision_record_writable": decision_record_writable,
        "decision_artifact_path": decision_artifact_path,
        "decision_artifact_exists": decision_exists or decision_written,
        "work_fingerprint": work_fingerprint,
        "expected_work_fingerprint": expected,
        "work_fingerprint_matched": (expected == work_fingerprint) if expected else None,
        "current_work_fingerprint": dry_run.get("current_work_fingerprint"),
        "current_approval_packet_id": dry_run.get("current_approval_packet_id"),
        "current_selected_runtime": dry_run.get("current_selected_runtime"),
        "current_selected_bus_name": dry_run.get("current_selected_bus_name"),
        "approved_selected_runtime": dry_run.get("approved_selected_runtime"),
        "approved_selected_bus_name": dry_run.get("approved_selected_bus_name"),
        "future_consumption_marker_path": dry_run.get("future_consumption_marker_path"),
        "approval_decision_only": True,
        "approval_granted": False,
        "approval_consumption_ready": False,
        "approval_consumed": False,
        "decision_consumed": False,
        "approval_consumption_marker_written": False,
        "agent_bus_task_written": False,
        "daemon_started": False,
        "runtime_dispatched": False,
        "provider_or_model_call_performed": False,
        "browser_or_external_action_performed": False,
        "governed_memory_write_performed": False,
        "canonical_writeback_performed": False,
        "approval_scope": dry_run.get("approval_scope") or {},
        "decision_artifact_preview": decision_payload,
        "source_consumption_dry_run": dry_run,
        "checks": checks,
        "blocked_effects": list(DECISION_BLOCKED_EFFECTS),
        "blockers": blockers,
        "errors": errors,
        "authority_flags": authority_flags,
        "next_recommended_pass": (
            "sub-agent-presets-approval-consumption-decision-binding"
            if ok and normalized_decision == "approved"
            else "sub-agent-presets-denied-approval-closeout"
            if ok and normalized_decision == "denied"
            else "sub-agent-presets-approval-review-decision-repair"
        ),
    }


def build_subagent_approval_consumption_decision_binding(
    approval_artifact_path: str | Path,
    decision_artifact_path: str | Path,
    *,
    expected_work_fingerprint: str | None = None,
    vault_root: str | Path | None = None,
    availability: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Validate a pending request plus recorded decision without consuming either."""

    root = Path(vault_root) if vault_root is not None else Path.cwd()
    approval_abs_path, approval_path_inside = _resolve_inside_vault(approval_artifact_path, root)
    approval_rel_path = _safe_relative(approval_abs_path, root)
    decision_abs_path, decision_path_inside = _resolve_inside_vault(decision_artifact_path, root)
    decision_rel_path = _safe_relative(decision_abs_path, root)
    expected = (expected_work_fingerprint or "").strip()
    blockers: list[str] = []
    errors: list[str] = []

    dry_run = build_subagent_approval_consumption_dry_run(
        approval_artifact_path,
        expected_work_fingerprint=expected,
        vault_root=root,
        availability=availability,
    )
    if not dry_run.get("ok"):
        blockers.extend(str(item) for item in dry_run.get("blockers") or [])
        errors.extend(str(item) for item in dry_run.get("errors") or [])

    approval_payload: dict[str, Any] | None = None
    approval_read_error: str | None = None
    if approval_path_inside and approval_abs_path.is_file():
        approval_payload, approval_read_error = _read_json_object(approval_abs_path)
        if approval_read_error:
            blockers.append(approval_read_error.split(":", 1)[0])
            errors.append(approval_read_error)

    approval = approval_payload or {}
    approval_scope = approval.get("approval_scope") if isinstance(approval.get("approval_scope"), dict) else {}
    approval_packet_id = str(approval.get("approval_packet_id") or dry_run.get("approval_packet_id") or "")
    work_fingerprint = str(approval.get("work_fingerprint") or dry_run.get("work_fingerprint") or "")

    decision_payload: dict[str, Any] | None = None
    decision_read_error: str | None = None
    if not decision_path_inside:
        blockers.append("approval_decision_artifact_path_outside_vault")
    elif not decision_abs_path.is_file():
        blockers.append("approval_decision_artifact_missing")
    else:
        decision_payload, decision_read_error = _read_json_object(
            decision_abs_path,
            artifact_label="approval_decision_artifact",
        )
        if decision_read_error:
            blockers.append(decision_read_error.split(":", 1)[0])
            errors.append(decision_read_error)

    decision = decision_payload or {}
    decision_operator = str(decision.get("operator_decision") or decision.get("decision") or "")
    decision_authority = (
        decision.get("authority_flags") if isinstance(decision.get("authority_flags"), dict) else {}
    )
    decision_digest = str(decision.get("decision_digest_sha256") or "")
    expected_decision_digest = (
        _stable_digest({**decision, "decision_digest_sha256": ""}) if decision_payload else ""
    )

    expected_decision_artifact_path = ""
    expected_decision_path_inside = False
    if approval_packet_id and _packet_id_path_safe(approval_packet_id):
        expected_decision_rel_path = SUBAGENT_APPROVAL_DECISION_ROOT / f"{approval_packet_id}.json"
        expected_decision_abs_path, expected_decision_path_inside = _resolve_inside_vault(
            expected_decision_rel_path,
            root,
        )
        expected_decision_artifact_path = _safe_relative(expected_decision_abs_path, root)
        if not expected_decision_path_inside:
            blockers.append("expected_approval_decision_artifact_path_outside_vault")
        if decision_rel_path != expected_decision_artifact_path:
            blockers.append("approval_decision_artifact_path_mismatch")

    if decision_payload is not None:
        if decision.get("schema_version") != SUBAGENT_APPROVAL_DECISION_SCHEMA_VERSION:
            blockers.append("approval_decision_schema_invalid")
        if decision.get("packet_kind") != SUBAGENT_APPROVAL_DECISION_PACKET_KIND:
            blockers.append("approval_decision_packet_kind_invalid")
        if decision.get("status") != "approval_decision_recorded":
            blockers.append("approval_decision_status_not_recorded")
        if decision.get("decision_status") != "recorded":
            blockers.append("approval_decision_record_status_invalid")
        if decision.get("approval_decision_written") is not True:
            blockers.append("approval_decision_not_written")
        if decision.get("approval_decision_only") is not True:
            blockers.append("approval_decision_not_decision_only")
        if decision.get("immutable") is not True or decision.get("append_only") is not True:
            blockers.append("approval_decision_not_immutable_append_only")
        if decision.get("approval_packet_id") != approval_packet_id:
            blockers.append("approval_decision_packet_id_mismatch")
        if decision.get("source_approval_artifact_path") != approval_rel_path:
            blockers.append("approval_decision_source_artifact_mismatch")
        if decision.get("source_approval_status") != SUBAGENT_APPROVAL_REQUEST_STATUS:
            blockers.append("approval_decision_source_status_invalid")
        if decision.get("source_approval_schema_version") != SUBAGENT_APPROVAL_REQUEST_SCHEMA_VERSION:
            blockers.append("approval_decision_source_schema_invalid")
        if decision.get("source_approval_packet_kind") != SUBAGENT_APPROVAL_REQUEST_PACKET_KIND:
            blockers.append("approval_decision_source_packet_kind_invalid")
        if decision.get("decision_artifact_path") != decision_rel_path:
            blockers.append("approval_decision_self_path_mismatch")
        if decision_operator == "denied":
            blockers.append("approval_decision_denied")
        elif decision_operator != "approved":
            blockers.append("approval_decision_not_approved")
        if decision.get("work_fingerprint") != work_fingerprint:
            blockers.append("approval_decision_work_fingerprint_mismatch")
        if expected and expected != work_fingerprint:
            blockers.append("expected_work_fingerprint_mismatch")
        if decision.get("approval_scope") != approval_scope:
            blockers.append("approval_decision_scope_mismatch")
        if decision.get("approval_consumption_allowed") is not False:
            blockers.append("approval_decision_allows_consumption")
        if decision.get("approval_consumed") is not False:
            blockers.append("approval_decision_already_consumed_or_ambiguous")
        if decision.get("decision_consumed") is not False:
            blockers.append("approval_decision_already_consumed")
        if decision.get("approval_consumption_marker_written") is not False:
            blockers.append("approval_decision_marker_state_invalid")
        if decision.get("agent_bus_task_written") is not False:
            blockers.append("approval_decision_agent_bus_task_state_invalid")
        if decision.get("daemon_started") is not False or decision.get("runtime_dispatched") is not False:
            blockers.append("approval_decision_execution_state_invalid")
        if decision_authority.get("approval_consumption_allowed") is not False:
            blockers.append("approval_decision_authority_does_not_block_consumption")
        if decision_authority.get("approval_consumption_marker_write_allowed") is not False:
            blockers.append("approval_decision_authority_does_not_block_marker_write")
        if decision_authority.get("agent_bus_enqueue_allowed") is not False:
            blockers.append("approval_decision_authority_does_not_block_agent_bus_enqueue")
        if decision_authority.get("runtime_dispatch_allowed") is not False:
            blockers.append("approval_decision_authority_does_not_block_runtime_dispatch")
        if not decision_digest:
            blockers.append("approval_decision_digest_missing")
        elif decision_digest != expected_decision_digest:
            blockers.append("approval_decision_digest_mismatch")

    dry_run_blockers = set(str(item) for item in dry_run.get("blockers") or [])
    non_decision_dry_run_blockers = dry_run_blockers - {"operator_approval_decision_required"}
    if non_decision_dry_run_blockers:
        blockers.extend(sorted(non_decision_dry_run_blockers))
    if "operator_approval_decision_required" not in dry_run_blockers:
        blockers.append("approval_request_not_at_operator_decision_boundary")

    blockers = sorted(set(blockers))
    approved_binding_ready = (
        decision_payload is not None
        and approval_payload is not None
        and decision_operator == "approved"
        and not blockers
    )
    denied_only = blockers == ["approval_decision_denied"]
    status = (
        "approval_consumption_decision_binding_ready_no_execution"
        if approved_binding_ready
        else "blocked_approval_decision_denied"
        if denied_only
        else "blocked"
    )

    checks = {
        "approval_artifact_path_in_vault": approval_path_inside,
        "approval_artifact_present": approval_abs_path.is_file() if approval_path_inside else False,
        "approval_artifact_json_valid": approval_payload is not None,
        "approval_request_valid_for_consumption_boundary": dry_run.get("ok"),
        "decision_artifact_path_in_vault": decision_path_inside,
        "decision_artifact_present": decision_abs_path.is_file() if decision_path_inside else False,
        "decision_artifact_json_valid": decision_payload is not None,
        "decision_schema_valid": decision.get("schema_version") == SUBAGENT_APPROVAL_DECISION_SCHEMA_VERSION,
        "decision_packet_kind_valid": decision.get("packet_kind") == SUBAGENT_APPROVAL_DECISION_PACKET_KIND,
        "decision_recorded": decision.get("decision_status") == "recorded",
        "decision_written": decision.get("approval_decision_written") is True,
        "decision_is_approved": decision_operator == "approved",
        "decision_packet_id_matches_request": decision.get("approval_packet_id") == approval_packet_id,
        "decision_source_artifact_matches_request": decision.get("source_approval_artifact_path") == approval_rel_path,
        "decision_artifact_path_matches_expected": (
            bool(expected_decision_artifact_path) and decision_rel_path == expected_decision_artifact_path
        ),
        "decision_work_fingerprint_matches_request": decision.get("work_fingerprint") == work_fingerprint,
        "expected_work_fingerprint_matches": (expected == work_fingerprint) if expected else None,
        "decision_scope_matches_request": decision.get("approval_scope") == approval_scope,
        "decision_digest_valid": bool(decision_digest and decision_digest == expected_decision_digest),
        "future_consumption_marker_absent": (dry_run.get("checks") or {}).get(
            "future_consumption_marker_absent"
        ),
        "no_writes_performed": True,
        "approval_consumption_blocked": True,
        "agent_bus_enqueue_blocked": True,
        "runtime_dispatch_blocked": True,
    }
    authority_flags = _approval_decision_binding_authority_flags()
    return {
        "ok": approved_binding_ready,
        "status": status,
        "command": "subagents.approval-consumption-decision-binding",
        "schema_version": SUBAGENT_APPROVAL_DECISION_BINDING_SCHEMA_VERSION,
        "packet_kind": SUBAGENT_APPROVAL_DECISION_BINDING_PACKET_KIND,
        "read_only": True,
        "preview_only": True,
        "writes_performed": False,
        "vault_root": str(root),
        "approval_artifact_path": approval_rel_path,
        "approval_artifact_loaded": approval_payload is not None,
        "decision_artifact_path": decision_rel_path,
        "decision_artifact_loaded": decision_payload is not None,
        "expected_decision_artifact_path": expected_decision_artifact_path,
        "approval_packet_id": approval_packet_id,
        "approval_request_status": approval.get("status"),
        "operator_decision": decision_operator,
        "decision": decision_operator,
        "reviewer_id": decision.get("reviewer_id"),
        "work_fingerprint": work_fingerprint,
        "expected_work_fingerprint": expected,
        "work_fingerprint_matched": (expected == work_fingerprint) if expected else None,
        "current_work_fingerprint": dry_run.get("current_work_fingerprint"),
        "current_approval_packet_id": dry_run.get("current_approval_packet_id"),
        "current_selected_runtime": dry_run.get("current_selected_runtime"),
        "current_selected_bus_name": dry_run.get("current_selected_bus_name"),
        "approved_selected_runtime": dry_run.get("approved_selected_runtime"),
        "approved_selected_bus_name": dry_run.get("approved_selected_bus_name"),
        "future_consumption_marker_path": dry_run.get("future_consumption_marker_path"),
        "future_consumption_marker_exists": dry_run.get("future_consumption_marker_exists"),
        "approval_decision_accepted": approved_binding_ready,
        "approval_consumption_preflight_ready": approved_binding_ready,
        "approval_consumption_ready": False,
        "approval_consumption_ready_for_future_executor": approved_binding_ready,
        "approval_granted": False,
        "approval_consumed": False,
        "decision_consumed": False,
        "approval_consumption_marker_written": False,
        "agent_bus_task_written": False,
        "daemon_started": False,
        "runtime_dispatched": False,
        "provider_or_model_call_performed": False,
        "browser_or_external_action_performed": False,
        "governed_memory_write_performed": False,
        "canonical_writeback_performed": False,
        "decision_digest_sha256": decision_digest,
        "decision_digest_matched": bool(decision_digest and decision_digest == expected_decision_digest),
        "approval_scope": approval_scope,
        "source_consumption_dry_run": dry_run,
        "decision_artifact": decision_payload,
        "checks": checks,
        "blocked_effects": list(DECISION_BLOCKED_EFFECTS),
        "blockers": blockers,
        "errors": errors,
        "authority_flags": authority_flags,
        "next_recommended_pass": (
            "sub-agent-presets-approval-consumption-exact-once-marker-contract"
            if approved_binding_ready
            else "sub-agent-presets-denied-approval-closeout"
            if denied_only
            else "sub-agent-presets-approval-consumption-decision-binding-repair"
        ),
    }


def build_subagent_approval_consumption_exact_once_marker_contract(
    approval_artifact_path: str | Path,
    decision_artifact_path: str | Path,
    *,
    expected_work_fingerprint: str | None = None,
    write_consumption_marker: bool = False,
    consumed_by: str = "operator",
    vault_root: str | Path | None = None,
    availability: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Preview or write the exact-once marker for a bound approved decision."""

    root = Path(vault_root) if vault_root is not None else Path.cwd()
    expected = (expected_work_fingerprint or "").strip()
    consumer = (consumed_by or "operator").strip() or "operator"
    blockers: list[str] = []
    errors: list[str] = []

    binding = build_subagent_approval_consumption_decision_binding(
        approval_artifact_path,
        decision_artifact_path,
        expected_work_fingerprint=expected,
        vault_root=root,
        availability=availability,
    )
    if not binding.get("ok"):
        blockers.extend(str(item) for item in binding.get("blockers") or [])
        errors.extend(str(item) for item in binding.get("errors") or [])

    approval_packet_id = str(binding.get("approval_packet_id") or "")
    work_fingerprint = str(binding.get("work_fingerprint") or "")
    approval_rel_path = str(binding.get("approval_artifact_path") or "")
    decision_rel_path = str(binding.get("decision_artifact_path") or "")
    decision_digest = str(binding.get("decision_digest_sha256") or "")
    marker_rel_path = ""
    marker_abs_path: Path | None = None
    marker_path_inside = False
    marker_exists = False
    marker_payload_existing: dict[str, Any] | None = None

    if approval_packet_id and _packet_id_path_safe(approval_packet_id):
        marker_rel = SUBAGENT_APPROVAL_CONSUMPTION_MARKER_ROOT / f"{approval_packet_id}.json"
        marker_abs_path, marker_path_inside = _resolve_inside_vault(marker_rel, root)
        marker_rel_path = _safe_relative(marker_abs_path, root)
        marker_exists = marker_abs_path.exists()
        if not marker_path_inside:
            blockers.append("approval_consumption_marker_path_outside_vault")
        if marker_exists:
            blockers.append("approval_consumption_marker_already_exists")
            marker_payload_existing, marker_read_error = _read_json_object(
                marker_abs_path,
                artifact_label="approval_consumption_marker",
            )
            if marker_read_error:
                blockers.append(marker_read_error.split(":", 1)[0])
                errors.append(marker_read_error)
    else:
        blockers.append("approval_packet_id_missing_or_unsafe_for_marker")

    if write_consumption_marker and not expected:
        blockers.append("expected_work_fingerprint_required_for_marker_write")
    if expected and expected != work_fingerprint:
        blockers.append("expected_work_fingerprint_mismatch")
    if not decision_digest:
        blockers.append("approval_decision_digest_missing")

    source_binding_status = str(binding.get("status") or "")
    marker_payload = {
        "schema_version": SUBAGENT_APPROVAL_CONSUMPTION_MARKER_SCHEMA_VERSION,
        "packet_kind": SUBAGENT_APPROVAL_CONSUMPTION_MARKER_PACKET_KIND,
        "approval_packet_id": approval_packet_id,
        "status": "approval_consumption_marker_preview",
        "marker_status": "preview",
        "created_at": datetime.now(UTC).isoformat(),
        "consumed_by": consumer,
        "vault_root": str(root),
        "consumption_policy": "exact_once",
        "marker_write_mode": "exclusive_create",
        "marker_contract_only": True,
        "source_binding_status": source_binding_status,
        "source_approval_artifact_path": approval_rel_path,
        "source_decision_artifact_path": decision_rel_path,
        "consumption_marker_path": marker_rel_path,
        "work_fingerprint": work_fingerprint,
        "expected_work_fingerprint": expected,
        "decision_digest_sha256": decision_digest,
        "operator_decision": binding.get("operator_decision"),
        "reviewer_id": binding.get("reviewer_id"),
        "approval_scope": binding.get("approval_scope") or {},
        "approved_selected_runtime": binding.get("approved_selected_runtime"),
        "approved_selected_bus_name": binding.get("approved_selected_bus_name"),
        "approval_decision_accepted": bool(binding.get("approval_decision_accepted")),
        "approval_request_mutated": False,
        "approval_decision_mutated": False,
        "approval_granted": False,
        "approval_consumed": False,
        "decision_consumed": False,
        "approval_consumption_marker_written": False,
        "approval_consumption_marker_reserved": False,
        "agent_bus_task_written": False,
        "daemon_started": False,
        "runtime_dispatched": False,
        "provider_or_model_call_performed": False,
        "browser_or_external_action_performed": False,
        "governed_memory_write_performed": False,
        "canonical_writeback_performed": False,
        "blocked_effects": [
            "agent_bus_task_creation",
            "daemon_start",
            "runtime_dispatch",
            "provider_or_model_call",
            "browser_or_external_action",
            "governed_memory_persistence",
            "canonical_state_change",
        ],
        "authority_flags": _approval_consumption_marker_authority_flags(marker_written=False),
        "next_recommended_pass": (
            "sub-agent-presets-agent-bus-task-packet-preview"
        ),
    }
    marker_payload["marker_digest_sha256"] = _stable_digest(
        {**marker_payload, "marker_digest_sha256": ""}
    )

    blockers = sorted(set(blockers))
    marker_record_writable = bool(
        binding.get("ok") and marker_abs_path is not None and marker_path_inside and not blockers
    )
    marker_written = False

    if write_consumption_marker and marker_record_writable and marker_abs_path is not None:
        written_payload = {
            **marker_payload,
            "status": "approval_consumption_marker_recorded_no_execution",
            "marker_status": "recorded",
            "approval_consumption_marker_written": True,
            "approval_consumption_marker_reserved": True,
            "authority_flags": _approval_consumption_marker_authority_flags(marker_written=True),
        }
        written_payload["marker_digest_sha256"] = _stable_digest(
            {**written_payload, "marker_digest_sha256": ""}
        )
        marker_abs_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with marker_abs_path.open("x", encoding="utf-8") as handle:
                handle.write(json.dumps(written_payload, indent=2, default=str) + "\n")
        except FileExistsError:
            blockers.append("approval_consumption_marker_already_exists")
        else:
            marker_written = True
            marker_payload = written_payload
            marker_exists = True

    if write_consumption_marker and not marker_written and not blockers:
        blockers.append("approval_consumption_marker_not_written_unknown_reason")
    blockers = sorted(set(blockers))

    ok = marker_written if write_consumption_marker else marker_record_writable
    duplicate_blocked = "approval_consumption_marker_already_exists" in blockers
    status = (
        "approval_consumption_marker_written_no_execution"
        if marker_written
        else "blocked_duplicate_approval_consumption_marker"
        if duplicate_blocked
        else "blocked"
        if blockers
        else "ready_to_write_approval_consumption_marker_no_execution"
    )
    checks = {
        "binding_ready": binding.get("ok"),
        "approved_decision_bound": binding.get("approval_decision_accepted"),
        "expected_work_fingerprint_required_for_write": bool(write_consumption_marker),
        "expected_work_fingerprint_matches": (expected == work_fingerprint) if expected else None,
        "decision_digest_present": bool(decision_digest),
        "marker_path_in_vault": marker_path_inside,
        "marker_absent_before_write": not marker_exists or marker_written,
        "marker_write_mode_exclusive_create": True,
        "marker_written": marker_written,
        "approval_request_mutated": False,
        "approval_decision_mutated": False,
        "agent_bus_enqueue_blocked": True,
        "runtime_dispatch_blocked": True,
        "provider_or_browser_action_blocked": True,
    }
    authority_flags = _approval_consumption_marker_authority_flags(marker_written=marker_written)
    return {
        "ok": ok,
        "status": status,
        "command": "subagents.approval-consumption-exact-once-marker-contract",
        "schema_version": SUBAGENT_APPROVAL_CONSUMPTION_MARKER_SCHEMA_VERSION,
        "packet_kind": SUBAGENT_APPROVAL_CONSUMPTION_MARKER_PACKET_KIND,
        "read_only": not marker_written,
        "preview_only": not marker_written,
        "writes_performed": marker_written,
        "vault_root": str(root),
        "approval_artifact_path": approval_rel_path,
        "decision_artifact_path": decision_rel_path,
        "approval_packet_id": approval_packet_id,
        "operator_decision": binding.get("operator_decision"),
        "work_fingerprint": work_fingerprint,
        "expected_work_fingerprint": expected,
        "work_fingerprint_matched": (expected == work_fingerprint) if expected else None,
        "decision_digest_sha256": decision_digest,
        "consumed_by": consumer,
        "write_consumption_marker_requested": write_consumption_marker,
        "marker_record_writable": marker_record_writable,
        "consumption_marker_path": marker_rel_path,
        "future_consumption_marker_path": marker_rel_path,
        "future_consumption_marker_exists": marker_exists,
        "approval_consumption_marker_written": marker_written,
        "approval_consumption_marker_reserved": marker_written,
        "approval_granted": False,
        "approval_consumed": False,
        "decision_consumed": False,
        "agent_bus_task_written": False,
        "daemon_started": False,
        "runtime_dispatched": False,
        "provider_or_model_call_performed": False,
        "browser_or_external_action_performed": False,
        "governed_memory_write_performed": False,
        "canonical_writeback_performed": False,
        "approval_scope": binding.get("approval_scope") or {},
        "source_decision_binding": binding,
        "consumption_marker_preview": marker_payload,
        "existing_consumption_marker": marker_payload_existing,
        "checks": checks,
        "blocked_effects": marker_payload.get("blocked_effects") or [],
        "blockers": blockers,
        "errors": errors,
        "authority_flags": authority_flags,
        "next_recommended_pass": (
            "sub-agent-presets-agent-bus-task-packet-preview"
            if marker_written
            else "sub-agent-presets-approval-consumption-exact-once-marker-write"
            if marker_record_writable
            else "sub-agent-presets-approval-consumption-exact-once-marker-repair"
        ),
    }


def build_subagent_agent_bus_task_packet_preview(
    approval_artifact_path: str | Path,
    decision_artifact_path: str | Path,
    *,
    consumption_marker_path: str | Path | None = None,
    expected_work_fingerprint: str | None = None,
    sender: str = "Operator",
    priority: str = "normal",
    vault_root: str | Path | None = None,
) -> dict[str, Any]:
    """Build an inert Agent Bus task packet preview after marker reservation."""

    root = Path(vault_root) if vault_root is not None else Path.cwd()
    expected = (expected_work_fingerprint or "").strip()
    requested_sender = (sender or "Operator").strip() or "Operator"
    task_priority = (priority or "normal").strip() or "normal"
    blockers: list[str] = []
    errors: list[str] = []

    approval_abs_path, approval_path_inside = _resolve_inside_vault(approval_artifact_path, root)
    approval_rel_path = _safe_relative(approval_abs_path, root)
    decision_abs_path, decision_path_inside = _resolve_inside_vault(decision_artifact_path, root)
    decision_rel_path = _safe_relative(decision_abs_path, root)

    approval_payload: dict[str, Any] | None = None
    decision_payload: dict[str, Any] | None = None
    marker_payload: dict[str, Any] | None = None

    if not approval_path_inside:
        blockers.append("approval_artifact_path_outside_vault")
    elif not approval_abs_path.is_file():
        blockers.append("approval_artifact_missing")
    else:
        approval_payload, read_error = _read_json_object(
            approval_abs_path,
            artifact_label="approval_artifact",
        )
        if read_error:
            blockers.append(read_error.split(":", 1)[0])
            errors.append(read_error)

    if not decision_path_inside:
        blockers.append("approval_decision_artifact_path_outside_vault")
    elif not decision_abs_path.is_file():
        blockers.append("approval_decision_artifact_missing")
    else:
        decision_payload, read_error = _read_json_object(
            decision_abs_path,
            artifact_label="approval_decision_artifact",
        )
        if read_error:
            blockers.append(read_error.split(":", 1)[0])
            errors.append(read_error)

    approval = approval_payload or {}
    decision = decision_payload or {}
    approval_scope = approval.get("approval_scope") if isinstance(approval.get("approval_scope"), dict) else {}
    approval_packet_id = str(approval.get("approval_packet_id") or "")
    work_fingerprint = str(approval.get("work_fingerprint") or "")
    decision_digest = str(decision.get("decision_digest_sha256") or "")
    expected_decision_digest = (
        _stable_digest({**decision, "decision_digest_sha256": ""}) if decision_payload else ""
    )

    if approval_payload is not None:
        if approval.get("schema_version") != SUBAGENT_APPROVAL_REQUEST_SCHEMA_VERSION:
            blockers.append("approval_artifact_schema_invalid")
        if approval.get("packet_kind") != SUBAGENT_APPROVAL_REQUEST_PACKET_KIND:
            blockers.append("approval_artifact_packet_kind_invalid")
        if approval.get("status") != SUBAGENT_APPROVAL_REQUEST_STATUS:
            blockers.append("approval_artifact_status_not_pending")
        if approval.get("agent_bus_task_written") is not False:
            blockers.append("approval_artifact_agent_bus_task_state_invalid")
        if not _packet_id_path_safe(approval_packet_id):
            blockers.append("approval_packet_id_missing_or_unsafe_for_task_packet")

    if decision_payload is not None:
        if decision.get("schema_version") != SUBAGENT_APPROVAL_DECISION_SCHEMA_VERSION:
            blockers.append("approval_decision_schema_invalid")
        if decision.get("packet_kind") != SUBAGENT_APPROVAL_DECISION_PACKET_KIND:
            blockers.append("approval_decision_packet_kind_invalid")
        if decision.get("status") != "approval_decision_recorded":
            blockers.append("approval_decision_status_not_recorded")
        if decision.get("approval_decision_written") is not True:
            blockers.append("approval_decision_not_written")
        if decision.get("operator_decision") != "approved":
            blockers.append("approval_decision_not_approved")
        if decision.get("approval_packet_id") != approval_packet_id:
            blockers.append("approval_decision_packet_id_mismatch")
        if decision.get("source_approval_artifact_path") != approval_rel_path:
            blockers.append("approval_decision_source_artifact_mismatch")
        if decision.get("work_fingerprint") != work_fingerprint:
            blockers.append("approval_decision_work_fingerprint_mismatch")
        if decision.get("approval_scope") != approval_scope:
            blockers.append("approval_decision_scope_mismatch")
        if not decision_digest:
            blockers.append("approval_decision_digest_missing")
        elif decision_digest != expected_decision_digest:
            blockers.append("approval_decision_digest_mismatch")
        if decision.get("agent_bus_task_written") is not False:
            blockers.append("approval_decision_agent_bus_task_state_invalid")

    if expected and expected != work_fingerprint:
        blockers.append("expected_work_fingerprint_mismatch")

    marker_rel_path = ""
    marker_abs_path: Path | None = None
    marker_path_inside = False
    if consumption_marker_path is not None:
        marker_abs_path, marker_path_inside = _resolve_inside_vault(consumption_marker_path, root)
        marker_rel_path = _safe_relative(marker_abs_path, root)
    elif approval_packet_id and _packet_id_path_safe(approval_packet_id):
        marker_rel = SUBAGENT_APPROVAL_CONSUMPTION_MARKER_ROOT / f"{approval_packet_id}.json"
        marker_abs_path, marker_path_inside = _resolve_inside_vault(marker_rel, root)
        marker_rel_path = _safe_relative(marker_abs_path, root)
    else:
        blockers.append("approval_consumption_marker_path_unavailable")

    marker_exists = bool(marker_abs_path and marker_abs_path.is_file())
    if marker_abs_path is not None:
        if not marker_path_inside:
            blockers.append("approval_consumption_marker_path_outside_vault")
        elif not marker_abs_path.is_file():
            blockers.append("approval_consumption_marker_missing")
        else:
            marker_payload, read_error = _read_json_object(
                marker_abs_path,
                artifact_label="approval_consumption_marker",
            )
            if read_error:
                blockers.append(read_error.split(":", 1)[0])
                errors.append(read_error)

    marker = marker_payload or {}
    marker_digest = str(marker.get("marker_digest_sha256") or "")
    expected_marker_digest = (
        _stable_digest({**marker, "marker_digest_sha256": ""}) if marker_payload else ""
    )
    if marker_payload is not None:
        if marker.get("schema_version") != SUBAGENT_APPROVAL_CONSUMPTION_MARKER_SCHEMA_VERSION:
            blockers.append("approval_consumption_marker_schema_invalid")
        if marker.get("packet_kind") != SUBAGENT_APPROVAL_CONSUMPTION_MARKER_PACKET_KIND:
            blockers.append("approval_consumption_marker_packet_kind_invalid")
        if marker.get("status") != "approval_consumption_marker_recorded_no_execution":
            blockers.append("approval_consumption_marker_status_not_recorded")
        if marker.get("marker_status") != "recorded":
            blockers.append("approval_consumption_marker_record_status_invalid")
        if marker.get("approval_consumption_marker_written") is not True:
            blockers.append("approval_consumption_marker_not_written")
        if marker.get("approval_consumption_marker_reserved") is not True:
            blockers.append("approval_consumption_marker_not_reserved")
        if marker.get("approval_packet_id") != approval_packet_id:
            blockers.append("approval_consumption_marker_packet_id_mismatch")
        if marker.get("source_approval_artifact_path") != approval_rel_path:
            blockers.append("approval_consumption_marker_source_approval_mismatch")
        if marker.get("source_decision_artifact_path") != decision_rel_path:
            blockers.append("approval_consumption_marker_source_decision_mismatch")
        if marker.get("work_fingerprint") != work_fingerprint:
            blockers.append("approval_consumption_marker_work_fingerprint_mismatch")
        if marker.get("decision_digest_sha256") != decision_digest:
            blockers.append("approval_consumption_marker_decision_digest_mismatch")
        if marker.get("agent_bus_task_written") is not False:
            blockers.append("approval_consumption_marker_agent_bus_task_state_invalid")
        if marker.get("daemon_started") is not False or marker.get("runtime_dispatched") is not False:
            blockers.append("approval_consumption_marker_execution_state_invalid")
        if not marker_digest:
            blockers.append("approval_consumption_marker_digest_missing")
        elif marker_digest != expected_marker_digest:
            blockers.append("approval_consumption_marker_digest_mismatch")

    selected_bus_name = str(
        approval_scope.get("selected_bus_name")
        or marker.get("approved_selected_bus_name")
        or decision.get("approved_selected_bus_name")
        or ""
    )
    selected_runtime = str(
        approval_scope.get("selected_runtime")
        or marker.get("approved_selected_runtime")
        or decision.get("approved_selected_runtime")
        or selected_bus_name
    )
    known_bus_names = _known_agent_bus_names(root)
    if not selected_bus_name:
        blockers.append("selected_bus_name_missing")
    elif selected_bus_name not in known_bus_names:
        blockers.append("selected_bus_name_not_registered_on_agent_bus")
    if requested_sender != "Operator" and requested_sender not in known_bus_names:
        blockers.append("sender_not_registered_on_agent_bus")
    if task_priority not in {"low", "normal", "high", "critical"}:
        blockers.append("agent_bus_task_priority_invalid")

    preset_id = str(approval_scope.get("preset_id") or "")
    mode = str(approval_scope.get("mode") or "")
    source_task_id = str(approval_scope.get("task_id") or "")
    objective = str(approval_scope.get("objective") or "")
    task_id = f"subagent-activation-task-{work_fingerprint[:16]}" if work_fingerprint else ""
    run_id = f"subagent-activation-run-{work_fingerprint[:16]}" if work_fingerprint else ""
    now = datetime.now(UTC).isoformat()
    notes_payload = {
        "task_type": SUBAGENT_AGENT_BUS_TASK_TYPE,
        "approval_packet_id": approval_packet_id,
        "approval_artifact_path": approval_rel_path,
        "decision_artifact_path": decision_rel_path,
        "consumption_marker_path": marker_rel_path,
        "preset_id": preset_id,
        "mode": mode,
        "source_task_id": source_task_id,
        "marker_contract_only": True,
        "agent_bus_task_packet_preview_only": True,
        "forbidden_actions": list(AGENT_BUS_TASK_PACKET_FORBIDDEN_ACTIONS),
        "allowed_result_shapes": list(SUBAGENT_AGENT_BUS_TASK_ALLOWED_RESULT_SHAPES),
    }
    task_packet_preview = {
        "task_id": task_id,
        "run_id": run_id,
        "reply_to": None,
        "from": requested_sender,
        "to": selected_bus_name,
        "intent": "TASK",
        "status": "open",
        "priority": task_priority,
        "owner": None,
        "owner_instance": None,
        "request": (
            f"Run sub-agent preset '{preset_id}' in mode '{mode}' for task '{source_task_id}'. "
            "Return a bounded result only; do not start daemons, call providers, use browsers, "
            "mutate governed memory, or write canonical state."
        ),
        "expected_output": (
            "A reviewable sub-agent result with one of the allowed result shapes "
            f"{', '.join(SUBAGENT_AGENT_BUS_TASK_ALLOWED_RESULT_SHAPES)} plus artifact references "
            "and explicit boundary confirmation."
        ),
        "depends_on": [approval_packet_id],
        "artifacts": [
            path
            for path in (approval_rel_path, decision_rel_path, marker_rel_path)
            if path
        ],
        "source_platform": "chaseos-subagents",
        "source_channel_id": None,
        "source_thread_id": None,
        "source_channel_class": "approval-consumption",
        "conversation_key": f"subagents:{approval_packet_id}" if approval_packet_id else None,
        "origin_message_id": approval_packet_id or None,
        "control_plane_route": "subagents.approval-consumption.agent-bus-task-packet-preview",
        "work_fingerprint": work_fingerprint or None,
        "execution_constraints": {
            "allow_shell_commands": False,
            "allow_live_subprocess": False,
            "allowed_write_paths": [],
            "write_policy": "none",
        },
        "notes": json.dumps(notes_payload, sort_keys=True),
        "created_at": now,
        "updated_at": now,
        "expires_at": None,
    }
    packet_shape_errors = _validate_agent_bus_task_packet_preview(task_packet_preview)
    if packet_shape_errors:
        blockers.extend(packet_shape_errors)

    blockers = sorted(set(blockers))
    ready = not blockers
    status = "agent_bus_task_packet_preview_ready_no_enqueue" if ready else "blocked"
    authority_flags = _agent_bus_task_packet_preview_authority_flags()
    checks = {
        "approval_artifact_path_in_vault": approval_path_inside,
        "approval_artifact_loaded": approval_payload is not None,
        "decision_artifact_path_in_vault": decision_path_inside,
        "decision_artifact_loaded": decision_payload is not None,
        "marker_path_in_vault": marker_path_inside,
        "marker_exists": marker_exists,
        "marker_loaded": marker_payload is not None,
        "marker_recorded": marker.get("marker_status") == "recorded",
        "marker_digest_valid": bool(marker_digest and marker_digest == expected_marker_digest),
        "decision_digest_valid": bool(decision_digest and decision_digest == expected_decision_digest),
        "expected_work_fingerprint_matches": (expected == work_fingerprint) if expected else None,
        "selected_bus_name_registered": selected_bus_name in known_bus_names if selected_bus_name else False,
        "agent_bus_task_packet_shape_valid": not packet_shape_errors,
        "agent_bus_task_written": False,
        "daemon_started": False,
        "runtime_dispatched": False,
        "provider_or_browser_action_blocked": True,
    }
    return {
        "ok": ready,
        "status": status,
        "command": "subagents.agent-bus-task-packet-preview",
        "schema_version": SUBAGENT_AGENT_BUS_TASK_PACKET_PREVIEW_SCHEMA_VERSION,
        "packet_kind": SUBAGENT_AGENT_BUS_TASK_PACKET_PREVIEW_PACKET_KIND,
        "read_only": True,
        "preview_only": True,
        "writes_performed": False,
        "vault_root": str(root),
        "approval_artifact_path": approval_rel_path,
        "decision_artifact_path": decision_rel_path,
        "consumption_marker_path": marker_rel_path,
        "approval_packet_id": approval_packet_id,
        "preset_id": preset_id,
        "mode": mode,
        "source_task_id": source_task_id,
        "objective": objective,
        "selected_runtime": selected_runtime,
        "selected_bus_name": selected_bus_name,
        "sender": requested_sender,
        "priority": task_priority,
        "work_fingerprint": work_fingerprint,
        "expected_work_fingerprint": expected,
        "work_fingerprint_matched": (expected == work_fingerprint) if expected else None,
        "decision_digest_sha256": decision_digest,
        "marker_digest_sha256": marker_digest,
        "task_type": SUBAGENT_AGENT_BUS_TASK_TYPE,
        "allowed_result_shapes": list(SUBAGENT_AGENT_BUS_TASK_ALLOWED_RESULT_SHAPES),
        "forbidden_actions": list(AGENT_BUS_TASK_PACKET_FORBIDDEN_ACTIONS),
        "agent_bus_task_packet_preview": task_packet_preview if ready else None,
        "agent_bus_task_packet_digest_sha256": _stable_digest(task_packet_preview) if ready else "",
        "agent_bus_task_preview_ready": ready,
        "agent_bus_task_written": False,
        "agent_bus_enqueue_performed": False,
        "daemon_started": False,
        "runtime_dispatched": False,
        "provider_or_model_call_performed": False,
        "browser_or_external_action_performed": False,
        "governed_memory_write_performed": False,
        "canonical_writeback_performed": False,
        "source_approval_artifact": approval_payload,
        "source_decision_artifact": decision_payload,
        "source_consumption_marker": marker_payload,
        "checks": checks,
        "blocked_effects": list(AGENT_BUS_TASK_PACKET_BLOCKED_EFFECTS),
        "blockers": blockers,
        "errors": errors,
        "authority_flags": authority_flags,
        "next_recommended_pass": (
            "sub-agent-presets-agent-bus-task-enqueue-writer"
            if ready
            else "sub-agent-presets-agent-bus-task-packet-preview-repair"
        ),
    }


def build_subagent_approval_request(
    preset_id: str,
    *,
    mode: str | None = None,
    task_id: str = "subagent-approval-request",
    objective: str = "Write a pending sub-agent activation approval request without consuming approval or dispatching.",
    requested_by: str = "operator",
    expected_work_fingerprint: str | None = None,
    write_approval_request: bool = False,
    vault_root: str | Path | None = None,
    availability: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build or optionally write a pending approval request for sub-agent activation."""

    root = Path(vault_root) if vault_root is not None else Path.cwd()
    preview = build_subagent_approval_packet_preview(
        preset_id,
        mode=mode,
        task_id=task_id,
        objective=objective,
        requested_by=requested_by,
        vault_root=root,
        availability=availability,
    )

    blockers = list(preview.get("blockers") or [])
    errors = list(preview.get("errors") or [])
    approval_packet_id = str(preview.get("approval_packet_id") or "")
    work_fingerprint = str(preview.get("work_fingerprint") or "")
    expected = (expected_work_fingerprint or "").strip()
    work_fingerprint_matched = bool(expected and expected == work_fingerprint)

    if not approval_packet_id:
        blockers.append("approval_packet_id_unavailable")
    if not work_fingerprint:
        blockers.append("work_fingerprint_unavailable")
    if expected and expected != work_fingerprint:
        blockers.append("expected_work_fingerprint_mismatch")
    if write_approval_request and not expected:
        blockers.append("expected_work_fingerprint_required_for_write")

    approval_rel_path = (
        SUBAGENT_APPROVAL_REQUEST_ROOT / f"{approval_packet_id or 'unknown-approval-packet'}.json"
    )
    approval_abs_path, approval_path_inside = _resolve_inside_vault(approval_rel_path, root)
    approval_artifact_path = _safe_relative(approval_abs_path, root)
    if not approval_path_inside:
        blockers.append("approval_artifact_path_outside_vault")

    artifact_preview = _approval_artifact_payload(
        approval_packet_id=approval_packet_id,
        requested_by=requested_by,
        vault_root=root,
        approval_preview=preview,
        approval_artifact_path=approval_artifact_path,
    )

    approval_artifact_written = False
    if write_approval_request:
        if not preview.get("ready_for_operator_decision"):
            blockers.append("approval_request_not_written_until_preview_ready")
        if approval_abs_path.exists():
            blockers.append("approval_artifact_already_exists_no_overwrite")
        if not blockers:
            approval_abs_path.parent.mkdir(parents=True, exist_ok=True)
            approval_abs_path.write_text(
                json.dumps(artifact_preview, indent=2, default=str) + "\n",
                encoding="utf-8",
            )
            approval_artifact_written = True

    blockers = sorted(set(blockers))
    ready_for_operator_decision = bool(preview.get("ready_for_operator_decision")) and not blockers
    ok = approval_artifact_written if write_approval_request else ready_for_operator_decision
    status = (
        "approval_request_written"
        if approval_artifact_written
        else "blocked"
        if blockers
        else "ready_for_operator_decision"
    )

    authority_flags = _approval_request_authority_flags(artifact_written=approval_artifact_written)
    artifact_preview = {
        **artifact_preview,
        "authority_flags": _approval_request_authority_flags(artifact_written=False),
    }

    return {
        "ok": ok,
        "status": status,
        "command": "subagents.write-approval-request",
        "schema_version": SUBAGENT_APPROVAL_REQUEST_SCHEMA_VERSION,
        "packet_kind": SUBAGENT_APPROVAL_REQUEST_PACKET_KIND,
        "preview_only": not approval_artifact_written,
        "writes_performed": approval_artifact_written,
        "vault_root": str(root),
        "requested_by": requested_by,
        "write_approval_request_requested": write_approval_request,
        "approval_request_written": approval_artifact_written,
        "approval_artifact_written": approval_artifact_written,
        "approval_packet_id": approval_packet_id,
        "work_fingerprint": work_fingerprint,
        "expected_work_fingerprint": expected,
        "work_fingerprint_matched": work_fingerprint_matched,
        "approval_artifact_path": approval_artifact_path,
        "approval_artifact_path_in_vault": approval_path_inside,
        "ready_for_operator_decision": ready_for_operator_decision,
        "operator_confirmation_text": artifact_preview.get("operator_confirmation_text"),
        "approval_artifact_preview": artifact_preview,
        "approval_packet_preview": preview.get("approval_packet_preview"),
        "source_approval_preview": preview,
        "approval_granted": False,
        "approval_consumed": False,
        "agent_bus_task_written": False,
        "daemon_started": False,
        "runtime_dispatched": False,
        "provider_or_model_call_performed": False,
        "browser_or_external_action_performed": False,
        "governed_memory_write_performed": False,
        "canonical_writeback_performed": False,
        "blocked_effects": list(BLOCKED_EFFECTS),
        "blockers": blockers,
        "errors": errors,
        "authority_flags": authority_flags,
        "next_recommended_pass": (
            "sub-agent-presets-approval-consumption-dry-run"
            if approval_artifact_written
            else "sub-agent-presets-approval-artifact-writer"
            if ready_for_operator_decision
            else "sub-agent-presets-approval-request-repair"
        ),
    }


def format_subagent_approval_packet_preview(payload: dict[str, Any]) -> str:
    preset = payload.get("preset") or {}
    route = payload.get("route") or {}
    lines = [
        "ChaseOS Sub-Agent Approval Packet Preview",
        f"  status: {payload.get('status')}",
        f"  approval_packet_id: {payload.get('approval_packet_id') or '(none)'}",
        f"  work_fingerprint: {payload.get('work_fingerprint') or '(none)'}",
        f"  preset: {preset.get('id') or payload.get('preset_id')} ({preset.get('name') or 'unknown'})",
        f"  mode: {(payload.get('task_scope') or {}).get('mode') or '(missing)'}",
        f"  selected_bus_name: {route.get('selected_bus_name') or '(none)'}",
        f"  ready_for_operator_decision: {payload.get('ready_for_operator_decision')}",
        f"  blockers: {', '.join(payload.get('blockers') or []) or '(none)'}",
        "  boundary: approval packet preview only; no approval artifact write, approval grant, approval consumption, Agent Bus task, daemon start, runtime dispatch, provider/model call, browser/external action, governed memory write, or canonical writeback.",
    ]
    if payload.get("errors"):
        lines.append(f"  errors: {'; '.join(payload.get('errors') or [])}")
    return "\n".join(lines)


def format_subagent_approval_request(payload: dict[str, Any]) -> str:
    lines = [
        "ChaseOS Sub-Agent Approval Request",
        f"  status: {payload.get('status')}",
        f"  approval_packet_id: {payload.get('approval_packet_id') or '(none)'}",
        f"  work_fingerprint: {payload.get('work_fingerprint') or '(none)'}",
        f"  expected_fingerprint_matched: {payload.get('work_fingerprint_matched')}",
        f"  request_written: {payload.get('approval_request_written')}",
        f"  artifact_path: {payload.get('approval_artifact_path') or '(none)'}",
        f"  ready_for_operator_decision: {payload.get('ready_for_operator_decision')}",
        f"  blockers: {', '.join(payload.get('blockers') or []) or '(none)'}",
        "  boundary: approval request only; no approval grant, approval consumption, Agent Bus task, daemon start, runtime dispatch, provider/model call, browser/external action, governed memory write, or canonical writeback.",
    ]
    if payload.get("operator_confirmation_text"):
        lines.extend(["  operator approval text:", payload.get("operator_confirmation_text") or ""])
    if payload.get("errors"):
        lines.append(f"  errors: {'; '.join(payload.get('errors') or [])}")
    return "\n".join(lines)


def format_subagent_approval_consumption_dry_run(payload: dict[str, Any]) -> str:
    lines = [
        "ChaseOS Sub-Agent Approval Consumption Dry Run",
        f"  status: {payload.get('status')}",
        f"  approval_packet_id: {payload.get('approval_packet_id') or '(none)'}",
        f"  work_fingerprint: {payload.get('work_fingerprint') or '(none)'}",
        f"  expected_fingerprint_matched: {payload.get('work_fingerprint_matched')}",
        f"  artifact_path: {payload.get('approval_artifact_path') or '(none)'}",
        f"  future_marker_path: {payload.get('future_consumption_marker_path') or '(none)'}",
        f"  approval_consumption_ready: {payload.get('approval_consumption_ready')}",
        f"  blockers: {', '.join(payload.get('blockers') or []) or '(none)'}",
        "  boundary: approval consumption dry-run only; no approval grant, no approval consumption, no marker write, no Agent Bus task, no daemon start, no runtime dispatch, no provider/model call, no browser/external action, no governed memory write, and no canonical writeback.",
    ]
    if payload.get("errors"):
        lines.append(f"  errors: {'; '.join(payload.get('errors') or [])}")
    return "\n".join(lines)


def format_subagent_approval_review_decision(payload: dict[str, Any]) -> str:
    lines = [
        "ChaseOS Sub-Agent Approval Review Decision",
        f"  status: {payload.get('status')}",
        f"  approval_packet_id: {payload.get('approval_packet_id') or '(none)'}",
        f"  decision: {payload.get('decision') or '(none)'}",
        f"  reviewer_id: {payload.get('reviewer_id') or '(none)'}",
        f"  work_fingerprint: {payload.get('work_fingerprint') or '(none)'}",
        f"  expected_fingerprint_matched: {payload.get('work_fingerprint_matched')}",
        f"  decision_record_writable: {payload.get('decision_record_writable')}",
        f"  decision_written: {payload.get('approval_decision_written')}",
        f"  decision_artifact_path: {payload.get('decision_artifact_path') or '(none)'}",
        f"  blockers: {', '.join(payload.get('blockers') or []) or '(none)'}",
        "  boundary: approval decision artifact only; no approval consumption, no marker write, no Agent Bus task, no daemon start, no runtime dispatch, no provider/model call, no browser/external action, no governed memory write, and no canonical writeback.",
    ]
    if payload.get("errors"):
        lines.append(f"  errors: {'; '.join(payload.get('errors') or [])}")
    return "\n".join(lines)


def format_subagent_approval_consumption_decision_binding(payload: dict[str, Any]) -> str:
    lines = [
        "ChaseOS Sub-Agent Approval Consumption Decision Binding",
        f"  status: {payload.get('status')}",
        f"  approval_packet_id: {payload.get('approval_packet_id') or '(none)'}",
        f"  decision: {payload.get('decision') or '(none)'}",
        f"  work_fingerprint: {payload.get('work_fingerprint') or '(none)'}",
        f"  expected_fingerprint_matched: {payload.get('work_fingerprint_matched')}",
        f"  approval_artifact_path: {payload.get('approval_artifact_path') or '(none)'}",
        f"  decision_artifact_path: {payload.get('decision_artifact_path') or '(none)'}",
        f"  future_marker_path: {payload.get('future_consumption_marker_path') or '(none)'}",
        f"  preflight_ready: {payload.get('approval_consumption_preflight_ready')}",
        f"  ready_for_future_executor: {payload.get('approval_consumption_ready_for_future_executor')}",
        f"  blockers: {', '.join(payload.get('blockers') or []) or '(none)'}",
        "  boundary: approval decision binding preflight only; no approval grant, no approval consumption, no marker write, no Agent Bus task, no daemon start, no runtime dispatch, no provider/model call, no browser/external action, no governed memory write, and no canonical writeback.",
    ]
    if payload.get("errors"):
        lines.append(f"  errors: {'; '.join(payload.get('errors') or [])}")
    return "\n".join(lines)


def format_subagent_approval_consumption_exact_once_marker_contract(
    payload: dict[str, Any],
) -> str:
    lines = [
        "ChaseOS Sub-Agent Approval Consumption Exact-Once Marker Contract",
        f"  status: {payload.get('status')}",
        f"  approval_packet_id: {payload.get('approval_packet_id') or '(none)'}",
        f"  decision: {payload.get('operator_decision') or '(none)'}",
        f"  work_fingerprint: {payload.get('work_fingerprint') or '(none)'}",
        f"  expected_fingerprint_matched: {payload.get('work_fingerprint_matched')}",
        f"  marker_record_writable: {payload.get('marker_record_writable')}",
        f"  marker_written: {payload.get('approval_consumption_marker_written')}",
        f"  marker_path: {payload.get('consumption_marker_path') or '(none)'}",
        f"  blockers: {', '.join(payload.get('blockers') or []) or '(none)'}",
        "  boundary: exact-once marker contract only; no approval request mutation, no decision artifact mutation, no Agent Bus task, no daemon start, no runtime dispatch, no provider/model call, no browser/external action, no governed memory write, and no canonical writeback.",
    ]
    if payload.get("errors"):
        lines.append(f"  errors: {'; '.join(payload.get('errors') or [])}")
    return "\n".join(lines)


def format_subagent_agent_bus_task_packet_preview(payload: dict[str, Any]) -> str:
    packet = payload.get("agent_bus_task_packet_preview") or {}
    lines = [
        "ChaseOS Sub-Agent Agent Bus Task Packet Preview",
        f"  status: {payload.get('status')}",
        f"  approval_packet_id: {payload.get('approval_packet_id') or '(none)'}",
        f"  work_fingerprint: {payload.get('work_fingerprint') or '(none)'}",
        f"  selected_bus_name: {payload.get('selected_bus_name') or '(none)'}",
        f"  task_id: {packet.get('task_id') or '(none)'}",
        f"  run_id: {packet.get('run_id') or '(none)'}",
        f"  marker_path: {payload.get('consumption_marker_path') or '(none)'}",
        f"  packet_ready: {payload.get('agent_bus_task_preview_ready')}",
        f"  task_written: {payload.get('agent_bus_task_written')}",
        f"  blockers: {', '.join(payload.get('blockers') or []) or '(none)'}",
        "  boundary: Agent Bus task packet preview only; no Agent Bus enqueue, no daemon start, no runtime dispatch, no provider/model call, no browser/external action, no governed memory write, and no canonical writeback.",
    ]
    if payload.get("errors"):
        lines.append(f"  errors: {'; '.join(payload.get('errors') or [])}")
    return "\n".join(lines)
