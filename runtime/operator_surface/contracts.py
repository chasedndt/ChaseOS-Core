"""
runtime.operator_surface.contracts

Core dataclasses for the FSOS shared runtime contract layer.
These are the shared schemas that all surface adapters and the executor operate on.

Defined in: 06_AGENTS/Full-System-Operator-Surface.md Section 6
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from runtime.operator_surface.capabilities import SurfaceType
from runtime.operator_surface.events import OperatorEvent


class SessionStatus(str, Enum):
    """Lifecycle status of an OperatorSession."""
    ACTIVE = "active"
    SUSPENDED = "suspended"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass
class OperatorScope:
    """
    Declares the execution scope for a single FSOS run.
    Every field is enforced at action time — the executor blocks
    any action that exceeds the declared scope.

    Scope is validated against the workflow manifest by AOR before dispatch.
    Scope cannot be expanded at runtime.
    """
    run_id: str
    surface: SurfaceType

    # Target declarations — explicit; no wildcards without Tier 1 grant
    target_uris: list[str] = field(default_factory=list)
    allowed_origins: list[str] = field(default_factory=list)    # browser: allowed domains
    allowed_paths: list[str] = field(default_factory=list)      # filesystem: allowed prefixes
    forbidden_zones: list[str] = field(default_factory=list)    # absolute exclusion list

    # Hard ceilings
    max_actions: int = 50
    max_duration_seconds: int = 300

    # Action class approval requirements
    requires_approval: list[str] = field(default_factory=list)  # action class names

    # Feature flags — false by default; require explicit declaration
    external_network: bool = False
    credential_access: bool = False

    def validate(self) -> list[str]:
        """
        Return a list of validation errors.
        Empty list means scope is valid.
        """
        errors = []
        if not self.target_uris and not self.allowed_origins and not self.allowed_paths:
            errors.append("Scope must declare at least one target (target_uris, allowed_origins, or allowed_paths)")
        if self.max_actions <= 0:
            errors.append("max_actions must be positive")
        if self.max_duration_seconds <= 0:
            errors.append("max_duration_seconds must be positive")
        if self.credential_access:
            # credential_access=True is a high-risk declaration; flag for review
            errors.append(
                "credential_access=True requires explicit Tier 1 grant and operator confirmation "
                "before dispatch. This must be approved before any run proceeds."
            )
        return errors


@dataclass
class StepResult:
    """Result of executing a single step in an FSOS plan."""
    step_index: int
    success: bool
    action_type: str
    target: str
    output: Optional[dict] = None   # structured output if step produces data
    error: Optional[str] = None
    grounding_mode_used: Optional[str] = None
    requires_approval: bool = False
    approval_id: Optional[str] = None


@dataclass
class RecoveryResult:
    """Result of a recovery attempt after a step failure."""
    attempted: bool
    success: bool
    recovery_actions: list[str] = field(default_factory=list)
    final_surface_state: str = "unknown"
    error: Optional[str] = None


@dataclass
class OperatorSession:
    """
    Runtime-local session state for an FSOS execution.

    This is ephemeral runtime state — NOT canonical memory.
    Session state is NOT written to the vault.
    Anything durable is written through the standard AOR → writeback → Gate chain.
    If the runtime restarts, the session is reconstructed from OperatorRunAudit.
    """
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    run_id: str = ""
    workflow_id: str = ""
    surface: str = ""
    scope: Optional[OperatorScope] = None
    status: SessionStatus = SessionStatus.ACTIVE
    current_step: int = 0
    total_steps: int = 0
    started_at: str = ""
    last_active: str = ""
    events: list[OperatorEvent] = field(default_factory=list)
    pending_approvals: list[str] = field(default_factory=list)
    actions_taken: int = 0


@dataclass
class ApprovalRecord:
    """
    Immutable record of an operator approval decision.
    Written to audit artifact before execution resumes.
    """
    approval_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    run_id: str = ""
    step_index: int = 0
    action_type: str = ""
    target: str = ""
    description: str = ""
    decision: str = ""          # "APPROVE" | "DENY"
    operator_note: str = ""
    timestamp: str = ""


@dataclass
class OperatorRunAudit:
    """
    Complete audit artifact for an FSOS run.
    Written to 07_LOGS/Agent-Activity/ on run close.
    Used for replay and post-mortem analysis.

    See: 06_AGENTS/Operator-Surface-Adapter-Spec.md Section 10
    """
    run_id: str
    workflow_id: str
    surface: str
    scope: Optional[OperatorScope] = None

    # Execution record
    plan: list[dict] = field(default_factory=list)
    events: list[OperatorEvent] = field(default_factory=list)
    approvals: list[ApprovalRecord] = field(default_factory=list)
    step_results: list[StepResult] = field(default_factory=list)
    recovery_results: list[RecoveryResult] = field(default_factory=list)

    # Outcome
    outcome: str = "UNKNOWN"    # COMPLETE | FAILED | DENIED | HALTED
    error: Optional[str] = None

    # Counters
    steps_planned: int = 0
    steps_completed: int = 0
    steps_failed: int = 0
    actions_taken: int = 0
    approvals_required: int = 0
    approvals_granted: int = 0
    approvals_denied: int = 0
    recovery_attempts: int = 0

    # Vault impact
    vault_writes: list[str] = field(default_factory=list)
    capture_ids: list[str] = field(default_factory=list)

    # Timing
    started_at: str = ""
    completed_at: str = ""

    # Surface-specific adapter fields (merged in by adapter.build_audit_payload())
    adapter_payload: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize audit to a JSON-compatible dict."""
        import dataclasses
        result = dataclasses.asdict(self)
        # Convert OperatorEvent and other nested dataclasses via their own dicts
        return result
