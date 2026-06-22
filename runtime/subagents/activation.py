"""Task-scoped sub-agent activation context builder."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from .models import CHASE_OS_MODES, SubAgentActivationContext, SubAgentPreset, SubAgentValidationError
from .router import SubAgentRuntimeRouter


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _summarize_payload(payload: Mapping[str, Any] | None) -> str:
    if not payload:
        return "no input payload"
    keys = sorted(str(key) for key in payload.keys())
    return f"input keys: {', '.join(keys)}"


class SubAgentActivationManager:
    """Creates bounded activation contexts; it does not execute sub-agents."""

    def __init__(
        self,
        *,
        vault_root: str | Path | None = None,
        router: SubAgentRuntimeRouter | None = None,
    ) -> None:
        self.vault_root = Path(vault_root) if vault_root is not None else Path.cwd()
        self.router = router or SubAgentRuntimeRouter(vault_root=self.vault_root)

    def build_activation_context(
        self,
        preset: SubAgentPreset,
        *,
        task_id: str,
        objective: str,
        mode: str,
        input_payload: Mapping[str, Any] | None = None,
        parent_agent_id: str = "",
        mission_id: str = "",
        activation_reason: str = "",
    ) -> SubAgentActivationContext:
        if mode not in CHASE_OS_MODES:
            raise SubAgentValidationError(f"invalid ChaseOS sub-agent mode: {mode!r}")
        if mode not in preset.modes:
            raise SubAgentValidationError(f"preset {preset.id!r} does not support mode {mode!r}")
        created_at = _utc_now()
        expires_at = created_at + timedelta(milliseconds=preset.lifecycle.ttl_ms)
        route = self.router.select_runtime(preset)
        state = "activated" if route.is_routable else "blocked"
        reason = activation_reason or ", ".join(preset.activation.triggers) or "manual"
        return SubAgentActivationContext(
            activation_id=f"subagent-{uuid4().hex[:12]}",
            preset_id=preset.id,
            preset_name=preset.name,
            task_id=task_id,
            objective=objective,
            mode=mode,
            state=state,
            selected_runtime=route.selected_runtime,
            selected_bus_name=route.selected_bus_name,
            fallback_runtimes=route.fallback_runtimes,
            unavailable_preferences=route.unavailable_preferences,
            blocked_reasons=route.blocked_reasons,
            activation_reason=reason,
            is_task_scoped=True,
            daemon_started=False,
            parent_agent_id=parent_agent_id,
            mission_id=mission_id,
            created_at=_iso(created_at),
            expires_at=_iso(expires_at),
            input_summary=_summarize_payload(input_payload),
            tool_policy=preset.tools,
            memory_policy=preset.memory,
            compute_budget=preset.compute,
            lifecycle_policy=preset.lifecycle,
            output_contract=preset.output,
            source_path=preset.source_path,
        )

    def checkpoint(
        self,
        context: SubAgentActivationContext,
        *,
        summary: str,
        status: str = "checkpoint",
    ) -> dict[str, Any]:
        return {
            "audit_event_type": "subagent.checkpoint",
            "activation_id": context.activation_id,
            "preset_id": context.preset_id,
            "task_id": context.task_id,
            "status": status,
            "summary": summary,
            "state": context.state,
            "context_persisted": False,
        }

    def teardown(
        self,
        context: SubAgentActivationContext,
        *,
        result_status: str = "completed",
    ) -> SubAgentActivationContext:
        next_state = "cleaned_up" if result_status in {"completed", "cancelled", "failed"} else result_status
        return replace(context, state=next_state)
