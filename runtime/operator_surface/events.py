"""
runtime.operator_surface.events

OperatorEvent schema and OperatorEventType enum.
All FSOS surface adapters emit events conforming to this schema.
Events are consumed by:
  - FSOS executor (session state management)
  - OSRIL event bus (operator-visible runtime interaction)
  - AOR audit trail (07_LOGS/Agent-Activity/)

Defined in: 06_AGENTS/Full-System-Operator-Surface.md Section 6.3
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class OperatorEventType(str, Enum):
    """
    All event types emitted by FSOS surface adapters.
    Adapter conformance requires emitting events at the correct lifecycle points.
    See: 06_AGENTS/Operator-Surface-Adapter-Spec.md Section 7
    """
    PLAN_READY = "plan_ready"               # Plan produced; before any execution
    STEP_STARTED = "step_started"           # Step begins
    STEP_COMPLETE = "step_complete"         # Step completed successfully
    STEP_FAILED = "step_failed"             # Step failed; triggers recovery
    AWAIT_APPROVAL = "await_approval"       # Execution paused; operator response required
    APPROVAL_RECEIVED = "approval_received" # Operator decision recorded
    RECOVERY_STARTED = "recovery_started"   # Recovery protocol active
    RECOVERY_COMPLETE = "recovery_complete" # Recovery succeeded
    SESSION_COMPLETE = "session_complete"   # Final event on success
    SESSION_FAILED = "session_failed"       # Final event on failure


@dataclass
class OperatorEvent:
    """
    Single event emitted during FSOS execution.

    Every event is linked to a run_id and surface.
    Events are ordered by step_index and timestamp.
    The full event sequence is the definitive record of what happened during a run.

    Adapters call emit_event(OperatorEvent(...)) — they do not own event storage.
    The executor stores events in the session and audit artifact.
    """
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    run_id: str = ""
    surface: str = ""                               # SurfaceType value
    event_type: OperatorEventType = OperatorEventType.STEP_STARTED
    timestamp: str = ""                             # ISO 8601
    step_index: int = 0
    action_class: Optional[str] = None             # what class of action triggered this
    description: str = ""                          # human-readable description
    payload: dict = field(default_factory=dict)    # surface-specific structured data
    approval_required: bool = False
    approval_id: Optional[str] = None              # set if approval_required=True
    grounding_mode: Optional[str] = None           # which grounding tier was used

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "run_id": self.run_id,
            "surface": self.surface,
            "event_type": self.event_type.value,
            "timestamp": self.timestamp,
            "step_index": self.step_index,
            "action_class": self.action_class,
            "description": self.description,
            "payload": self.payload,
            "approval_required": self.approval_required,
            "approval_id": self.approval_id,
            "grounding_mode": self.grounding_mode,
        }
