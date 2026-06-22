"""
runtime.operator_surface — Full-System Operator Surface (FSOS)

Parent runtime package for governed, auditable computer action across
browser, terminal, desktop, and filesystem execution surfaces.

Architecture: 06_AGENTS/Full-System-Operator-Surface.md
Adapter spec:  06_AGENTS/Operator-Surface-Adapter-Spec.md
Browser spec:  06_AGENTS/Browser-Operator-Surface.md
Safety SOP:    04_SOPS/Full-System-Operator-Safety-SOP.md

Phase 9 — Full-System Operator Surface sub-track
Status: foothold — contracts and stubs created; browser adapter is first non-stub target
"""

from runtime.operator_surface.contracts import (
    OperatorScope,
    OperatorSession,
    OperatorRunAudit,
    SessionStatus,
    StepResult,
    RecoveryResult,
)
from runtime.operator_surface.events import (
    OperatorEvent,
    OperatorEventType,
)
from runtime.operator_surface.capabilities import (
    OperatorCapability,
    SurfaceType,
    GroundingMode,
)

__all__ = [
    "OperatorScope",
    "OperatorSession",
    "OperatorRunAudit",
    "SessionStatus",
    "StepResult",
    "RecoveryResult",
    "OperatorEvent",
    "OperatorEventType",
    "OperatorCapability",
    "SurfaceType",
    "GroundingMode",
]
