"""Read-only CLI payload builders for ChaseOS sub-agent presets."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .approval_packet import (
    build_subagent_agent_bus_task_packet_preview,
    build_subagent_approval_consumption_decision_binding,
    build_subagent_approval_consumption_dry_run,
    build_subagent_approval_consumption_exact_once_marker_contract,
    build_subagent_approval_packet_preview,
    build_subagent_approval_review_decision,
    build_subagent_approval_request,
    format_subagent_agent_bus_task_packet_preview,
    format_subagent_approval_consumption_decision_binding,
    format_subagent_approval_consumption_dry_run,
    format_subagent_approval_consumption_exact_once_marker_contract,
    format_subagent_approval_packet_preview,
    format_subagent_approval_review_decision,
    format_subagent_approval_request,
)
from .activation import SubAgentActivationManager
from .models import SubAgentValidationError
from .registry import SubAgentRegistry
from .router import SubAgentRuntimeRouter, build_runtime_availability


def _root(vault_root: str | Path | None = None) -> Path:
    return Path(vault_root) if vault_root is not None else Path.cwd()


def _authority_flags() -> dict[str, bool]:
    return {
        "read_only": True,
        "starts_daemon": False,
        "agent_bus_enqueue_allowed": False,
        "runtime_dispatch_allowed": False,
        "provider_call_allowed": False,
        "browser_launch_allowed": False,
        "governed_memory_write_allowed": False,
        "canonical_writeback_allowed": False,
    }


def _preset_summary(preset) -> dict[str, Any]:
    return {
        "id": preset.id,
        "name": preset.name,
        "role": preset.role,
        "description": preset.description,
        "modes": list(preset.modes),
        "runtime_preferences": list(preset.runtime_preferences),
        "source_path": preset.source_path,
        "output_format": preset.output.format,
        "required_sections": list(preset.output.required_sections),
        "max_tokens": preset.compute.max_tokens,
        "max_runtime_ms": preset.compute.max_runtime_ms,
        "max_parallel_workers": preset.compute.max_parallel_workers,
        "max_retries": preset.compute.max_retries,
    }


def build_subagent_list(vault_root: str | Path | None = None) -> dict[str, Any]:
    root = _root(vault_root)
    registry = SubAgentRegistry(vault_root=root)
    presets = registry.list_presets()
    return {
        "ok": True,
        "status": "ok",
        "command": "subagents.list",
        "read_only": True,
        "writes_performed": False,
        "vault_root": str(root),
        "preset_count": len(presets),
        "presets": [_preset_summary(preset) for preset in presets],
        "authority_flags": _authority_flags(),
    }


def build_subagent_show(preset_id: str, vault_root: str | Path | None = None) -> dict[str, Any]:
    root = _root(vault_root)
    registry = SubAgentRegistry(vault_root=root)
    try:
        preset = registry.get_preset(preset_id)
    except KeyError as exc:
        return {
            "ok": False,
            "status": "unknown_preset",
            "command": "subagents.show",
            "read_only": True,
            "writes_performed": False,
            "preset_id": preset_id,
            "errors": [str(exc)],
            "authority_flags": _authority_flags(),
        }
    return {
        "ok": True,
        "status": "ok",
        "command": "subagents.show",
        "read_only": True,
        "writes_performed": False,
        "preset": preset.to_dict(),
        "summary": _preset_summary(preset),
        "authority_flags": _authority_flags(),
    }


def build_subagent_validation(vault_root: str | Path | None = None) -> dict[str, Any]:
    root = _root(vault_root)
    registry = SubAgentRegistry(vault_root=root)
    errors = list(registry.validate_all())
    presets = [] if errors else registry.list_presets()
    availability = build_runtime_availability(root)
    return {
        "ok": not errors,
        "status": "ok" if not errors else "invalid",
        "command": "subagents.validate",
        "read_only": True,
        "writes_performed": False,
        "vault_root": str(root),
        "preset_count": len(presets),
        "error_count": len(errors),
        "errors": errors,
        "runtime_availability": availability,
        "authority_flags": _authority_flags(),
    }


def build_subagent_route_preview(
    preset_id: str,
    *,
    mode: str | None = None,
    task_id: str = "subagent-route-preview",
    objective: str = "Preview sub-agent routing without dispatch.",
    vault_root: str | Path | None = None,
) -> dict[str, Any]:
    root = _root(vault_root)
    registry = SubAgentRegistry(vault_root=root)
    try:
        preset = registry.get_preset(preset_id)
    except KeyError as exc:
        return {
            "ok": False,
            "status": "unknown_preset",
            "command": "subagents.route-preview",
            "read_only": True,
            "writes_performed": False,
            "preset_id": preset_id,
            "errors": [str(exc)],
            "authority_flags": _authority_flags(),
        }
    router = SubAgentRuntimeRouter(vault_root=root)
    route = router.select_runtime(preset)
    payload: dict[str, Any] = {
        "ok": route.is_routable,
        "status": route.route_status,
        "command": "subagents.route-preview",
        "read_only": True,
        "writes_performed": False,
        "preset": _preset_summary(preset),
        "route": {
            "route_status": route.route_status,
            "selected_runtime": route.selected_runtime,
            "selected_bus_name": route.selected_bus_name,
            "fallback_runtimes": list(route.fallback_runtimes),
            "unavailable_preferences": list(route.unavailable_preferences),
            "blocked_reasons": list(route.blocked_reasons),
        },
        "activation_context_preview": None,
        "authority_flags": _authority_flags(),
    }
    if mode:
        try:
            context = SubAgentActivationManager(vault_root=root, router=router).build_activation_context(
                preset,
                task_id=task_id,
                objective=objective,
                mode=mode,
                input_payload={"cli": "route-preview"},
                activation_reason="cli route-preview",
            )
        except SubAgentValidationError as exc:
            payload.update(
                {
                    "ok": False,
                    "status": "mode_invalid",
                    "errors": [str(exc)],
                }
            )
        else:
            payload["activation_context_preview"] = context.to_dict()
            payload["status"] = context.state
            payload["ok"] = context.state == "activated"
    return payload


def format_subagent_list(payload: dict[str, Any]) -> str:
    lines = [
        "ChaseOS Sub-Agent Presets",
        f"  status: {payload.get('status')}",
        f"  preset_count: {payload.get('preset_count', 0)}",
        "  boundary: read-only; no daemon, Agent Bus enqueue, provider call, browser launch, or governed memory write",
    ]
    for item in payload.get("presets") or []:
        modes = ",".join(item.get("modes") or [])
        runtimes = ",".join(item.get("runtime_preferences") or [])
        lines.append(f"  - {item.get('id')}: {item.get('name')} modes={modes} runtimes={runtimes}")
    return "\n".join(lines)


def format_subagent_show(payload: dict[str, Any]) -> str:
    if not payload.get("ok"):
        return f"ChaseOS Sub-Agent Preset\n  status: {payload.get('status')}\n  errors: {', '.join(payload.get('errors') or [])}"
    summary = payload.get("summary") or {}
    return "\n".join(
        [
            "ChaseOS Sub-Agent Preset",
            f"  id: {summary.get('id')}",
            f"  name: {summary.get('name')}",
            f"  role: {summary.get('role')}",
            f"  modes: {', '.join(summary.get('modes') or [])}",
            f"  runtime_preferences: {', '.join(summary.get('runtime_preferences') or [])}",
            f"  output_format: {summary.get('output_format')}",
            f"  source_path: {summary.get('source_path')}",
            "  boundary: read-only preset inspection only",
        ]
    )


def format_subagent_validation(payload: dict[str, Any]) -> str:
    lines = [
        "ChaseOS Sub-Agent Preset Validation",
        f"  status: {payload.get('status')}",
        f"  preset_count: {payload.get('preset_count', 0)}",
        f"  error_count: {payload.get('error_count', 0)}",
    ]
    for error in payload.get("errors") or []:
        lines.append(f"  - {error}")
    lines.append("  boundary: validation only; no execution or writeback")
    return "\n".join(lines)


def format_subagent_route_preview(payload: dict[str, Any]) -> str:
    preset = payload.get("preset") or {}
    route = payload.get("route") or {}
    lines = [
        "ChaseOS Sub-Agent Route Preview",
        f"  status: {payload.get('status')}",
        f"  preset: {preset.get('id')} ({preset.get('name')})",
        f"  selected_runtime: {route.get('selected_runtime') or '(none)'}",
        f"  selected_bus_name: {route.get('selected_bus_name') or '(none)'}",
    ]
    if route.get("unavailable_preferences"):
        lines.append(f"  unavailable_preferences: {', '.join(route.get('unavailable_preferences') or [])}")
    if route.get("blocked_reasons"):
        lines.append(f"  blocked_reasons: {'; '.join(route.get('blocked_reasons') or [])}")
    activation = payload.get("activation_context_preview") or {}
    if activation:
        lines.append(f"  activation_state: {activation.get('state')}")
        lines.append(f"  daemon_started: {activation.get('daemon_started')}")
        lines.append(f"  is_task_scoped: {activation.get('is_task_scoped')}")
    if payload.get("errors"):
        lines.append(f"  errors: {'; '.join(payload.get('errors') or [])}")
    lines.append("  boundary: route preview only; no Agent Bus task, runtime dispatch, provider call, browser launch, or governed memory write")
    return "\n".join(lines)
