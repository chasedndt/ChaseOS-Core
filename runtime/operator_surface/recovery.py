"""
runtime.operator_surface.recovery

Shared recovery protocol and helpers for FSOS execution.
Each surface adapter implements surface-specific recovery semantics,
but the protocol structure is shared.

Recovery model defined in: 06_AGENTS/Full-System-Operator-Surface.md Section 6.6
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Callable

from runtime.operator_surface.contracts import RecoveryResult, StepResult
from runtime.operator_surface.events import OperatorEvent, OperatorEventType


class UnrecoverableFailure(Exception):
    """Raised when recovery cannot restore the surface to a known-good state."""
    def __init__(self, step_index: int, surface: str, reason: str):
        self.step_index = step_index
        self.surface = surface
        self.reason = reason
        super().__init__(
            f"Unrecoverable failure on surface '{surface}' at step {step_index}: {reason}. "
            "Execution halted. Vault state unchanged."
        )


def build_recovery_started_event(
    run_id: str,
    surface: str,
    step_index: int,
    failed_step: dict,
    timestamp: str,
) -> OperatorEvent:
    return OperatorEvent(
        run_id=run_id,
        surface=surface,
        event_type=OperatorEventType.RECOVERY_STARTED,
        timestamp=timestamp,
        step_index=step_index,
        description=f"Recovery started after failure at step {step_index}",
        payload={"failed_step": failed_step},
    )


def build_recovery_complete_event(
    run_id: str,
    surface: str,
    step_index: int,
    recovery_actions: list[str],
    timestamp: str,
) -> OperatorEvent:
    return OperatorEvent(
        run_id=run_id,
        surface=surface,
        event_type=OperatorEventType.RECOVERY_COMPLETE,
        timestamp=timestamp,
        step_index=step_index,
        description=f"Recovery complete at step {step_index}",
        payload={"recovery_actions": recovery_actions},
    )


def build_session_failed_event(
    run_id: str,
    surface: str,
    step_index: int,
    reason: str,
    timestamp: str,
) -> OperatorEvent:
    return OperatorEvent(
        run_id=run_id,
        surface=surface,
        event_type=OperatorEventType.SESSION_FAILED,
        timestamp=timestamp,
        step_index=step_index,
        description=f"Session failed: {reason}",
        payload={"reason": reason},
    )


# ── Recovery protocol skeleton ────────────────────────────────────────────
# Surface adapters implement their own recover() using this protocol as a guide.

RECOVERY_PROTOCOL_STEPS = [
    "1. Emit RECOVERY_STARTED event",
    "2. Attempt surface-specific cleanup (close tabs/processes/windows)",
    "3. Return surface to last known-good state",
    "4. If cleanup succeeds: emit RECOVERY_COMPLETE",
    "5. If cleanup fails: raise UnrecoverableFailure",
    "6. Never leave vault in partial-write state",
]
