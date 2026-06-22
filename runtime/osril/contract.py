from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


_ALLOWED_STATES = {
    "idle",
    "working",
    "waiting_approval",
    "halted",
    "complete",
    "failed",
}


class OSRILEventType(str, Enum):
    STATUS = "status"
    TASK_STARTED = "task_started"
    TASK_PROGRESS = "task_progress"
    APPROVAL_REQUIRED = "approval_required"
    APPROVAL_RESPONSE = "approval_response"
    TASK_COMPLETE = "task_complete"
    TASK_FAILED = "task_failed"


@dataclass
class OSRILEvent:
    session_id: str
    run_id: str
    runtime_id: str
    workflow_id: str
    event_type: OSRILEventType
    timestamp: str
    state: str
    payload: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    permission_ceiling: Optional[str] = None

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.session_id:
            errors.append("session_id is required")
        if not self.run_id:
            errors.append("run_id is required")
        if not self.runtime_id:
            errors.append("runtime_id is required")
        if not self.workflow_id:
            errors.append("workflow_id is required")
        if not self.timestamp:
            errors.append("timestamp is required")
        if self.state not in _ALLOWED_STATES:
            errors.append(f"state must be one of {sorted(_ALLOWED_STATES)}")
        if self.event_type == OSRILEventType.APPROVAL_REQUIRED and not self.payload.get("approval_id"):
            errors.append("approval_required events must include payload.approval_id")
        if self.event_type == OSRILEventType.APPROVAL_RESPONSE:
            if not self.payload.get("approval_id"):
                errors.append("approval_response events must include payload.approval_id")
            if not self.payload.get("response_id"):
                errors.append("approval_response events must include payload.response_id")
            if self.payload.get("decision") not in {"APPROVE", "DENY"}:
                errors.append("approval_response events must include payload.decision APPROVE or DENY")
        if self.event_type == OSRILEventType.TASK_STARTED and not (self.permission_ceiling or self.payload.get("permission_ceiling")):
            errors.append("task_started events must include permission_ceiling")
        return errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "runtime_id": self.runtime_id,
            "workflow_id": self.workflow_id,
            "event_type": self.event_type.value,
            "timestamp": self.timestamp,
            "state": self.state,
            "permission_ceiling": self.permission_ceiling,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OSRILEvent":
        return cls(
            event_id=str(data.get("event_id") or str(uuid.uuid4())),
            session_id=str(data.get("session_id") or ""),
            run_id=str(data.get("run_id") or ""),
            runtime_id=str(data.get("runtime_id") or ""),
            workflow_id=str(data.get("workflow_id") or ""),
            event_type=OSRILEventType(str(data.get("event_type") or OSRILEventType.STATUS.value)),
            timestamp=str(data.get("timestamp") or ""),
            state=str(data.get("state") or "idle"),
            permission_ceiling=data.get("permission_ceiling"),
            payload=dict(data.get("payload") or {}),
        )
