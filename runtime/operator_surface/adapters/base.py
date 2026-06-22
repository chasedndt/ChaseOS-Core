"""
runtime.operator_surface.adapters.base

Abstract base class for all FSOS surface adapters.
Every surface adapter must inherit from OperatorSurfaceAdapterBase
and implement all abstract methods.

Conformance contract defined in: 06_AGENTS/Operator-Surface-Adapter-Spec.md
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, Optional

from runtime.operator_surface.capabilities import (
    OperatorCapability,
    SurfaceType,
    GroundingMode,
)
from runtime.operator_surface.contracts import (
    OperatorScope,
    OperatorSession,
    StepResult,
    RecoveryResult,
)
from runtime.operator_surface.events import OperatorEvent


class OperatorSurfaceAdapterBase(ABC):
    """
    Abstract base for all FSOS surface adapters.

    Subclasses must declare class-level attributes:
        ADAPTER_ID: str
        SURFACE_TYPE: SurfaceType
        ADAPTER_VERSION: str
        ADAPTER_STATUS: str          # "active" | "stub" | "partial"
        DESCRIPTION: str
        CAPABILITIES: frozenset[OperatorCapability]
        REQUIRED_SCOPE_FIELDS: frozenset[str]
        FORBIDDEN_SCOPE_PROPERTIES: frozenset[str]
        MIN_TRUST_TIER: int
        APPROVAL_REQUIRED_ACTIONS: frozenset[str]
        GROUNDING_MODES: list[GroundingMode]  # visual surfaces only; [] for text-only

    And implement all six abstract methods.
    """

    # ── Class-level identity (must be overridden by subclasses) ──────────────
    ADAPTER_ID: str = "base"
    SURFACE_TYPE: SurfaceType = NotImplemented
    ADAPTER_VERSION: str = "0.0.0"
    ADAPTER_STATUS: str = "stub"
    DESCRIPTION: str = "Base adapter — not for direct use"
    CAPABILITIES: frozenset = frozenset()
    REQUIRED_SCOPE_FIELDS: frozenset = frozenset()
    FORBIDDEN_SCOPE_PROPERTIES: frozenset = frozenset()
    MIN_TRUST_TIER: int = 2
    APPROVAL_REQUIRED_ACTIONS: frozenset = frozenset()
    GROUNDING_MODES: list = []

    def validate_scope(self, scope: OperatorScope) -> list[str]:
        """
        Validate that the provided scope satisfies this adapter's requirements.
        Returns list of error strings; empty = valid.
        """
        errors = []
        for field in self.REQUIRED_SCOPE_FIELDS:
            val = getattr(scope, field, None)
            if not val:
                errors.append(f"Scope missing required field '{field}' for adapter '{self.ADAPTER_ID}'")
        for prop in self.FORBIDDEN_SCOPE_PROPERTIES:
            if getattr(scope, prop, False):
                errors.append(
                    f"Scope property '{prop}' is forbidden for adapter '{self.ADAPTER_ID}'"
                )
        return errors

    @abstractmethod
    def initialize(self, scope: OperatorScope, session: OperatorSession) -> None:
        """
        Set up the surface context for this run.
        For browser: open an isolated browser context.
        For terminal: connect to or spawn a shell.
        For desktop: connect to accessibility API.
        For filesystem: validate allowed_paths exist.
        Must not fail silently — raise on initialization failure.
        """

    @abstractmethod
    def plan(self, goal: str, context: dict) -> list[dict]:
        """
        Produce an ordered list of steps to accomplish goal within scope.
        Each step must have 'action_type', 'target', and 'description' fields.
        In Phase 9 foothold: callers pass pre-declared manifest steps via executor.
        This method is for future dynamic planning from a goal string.
        """

    @abstractmethod
    def execute_step(self, step: dict, emit_event: Callable[[OperatorEvent], None]) -> StepResult:
        """
        Execute a single step.
        Must:
        - Emit STEP_STARTED before action (executor emits this; adapter receives emit_event)
        - Execute the action
        - Return StepResult with success status and any output
        - Never swallow exceptions — let them propagate to the executor
        """

    @abstractmethod
    def recover(self, failed_step: dict, emit_event: Callable[[OperatorEvent], None]) -> RecoveryResult:
        """
        Attempt recovery after a step failure.
        Must:
        - Emit RECOVERY_STARTED
        - Attempt surface-specific cleanup
        - Emit RECOVERY_COMPLETE if successful, or raise UnrecoverableFailure
        - Return RecoveryResult describing what was done
        """

    @abstractmethod
    def teardown(self, outcome: str, emit_event: Callable[[OperatorEvent], None]) -> None:
        """
        Clean up surface state after run completion or failure.
        Always called — even on failure.
        Must leave the surface in a clean state.
        For browser: close context.
        For terminal: terminate spawned processes.
        For desktop: release any held focus.
        For filesystem: release any file locks.
        """

    @abstractmethod
    def build_audit_payload(self) -> dict:
        """
        Return surface-specific fields to be merged into OperatorRunAudit.adapter_payload.
        Must include at minimum:
          adapter_id, surface_type, capabilities_used, steps_planned, steps_completed,
          steps_failed, approvals_required, approvals_granted, approvals_denied,
          recovery_attempts
        """

    def get_identity(self) -> dict:
        """Return adapter identity summary for registry listing."""
        return {
            "adapter_id": self.ADAPTER_ID,
            "surface_type": self.SURFACE_TYPE.value if hasattr(self.SURFACE_TYPE, "value") else str(self.SURFACE_TYPE),
            "adapter_version": self.ADAPTER_VERSION,
            "adapter_status": self.ADAPTER_STATUS,
            "description": self.DESCRIPTION,
            "capabilities": [c.value for c in self.CAPABILITIES],
            "min_trust_tier": self.MIN_TRUST_TIER,
        }
