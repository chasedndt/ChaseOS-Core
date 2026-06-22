"""Audit event shaping for sub-agent activations."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from .models import SubAgentActivationContext


def build_activation_audit_event(
    context: SubAgentActivationContext,
    *,
    event_type: str,
    message: str = "",
) -> dict[str, Any]:
    return {
        "event_type": event_type,
        "activation_id": context.activation_id,
        "preset_id": context.preset_id,
        "task_id": context.task_id,
        "mode": context.mode,
        "state": context.state,
        "selected_runtime": context.selected_runtime,
        "selected_bus_name": context.selected_bus_name,
        "daemon_started": context.daemon_started,
        "is_task_scoped": context.is_task_scoped,
        "message": message,
    }


def suggest_activation_audit_path(
    context: SubAgentActivationContext,
    *,
    vault_root: str | Path = ".",
    log_date: date | None = None,
) -> Path:
    current_date = log_date or date.today()
    filename = f"{current_date.isoformat()}-subagent-{context.activation_id}.json"
    return Path(vault_root) / "07_LOGS" / "Agent-Activity" / filename
