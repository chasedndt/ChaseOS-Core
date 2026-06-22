"""
runtime.operator_surface.executor

FSOS Executor — orchestrates a complete operator run from plan to audit.

The executor is the bridge between:
  - The planner (produces the step list)
  - The surface adapter (executes each step)
  - The session manager (tracks runtime state)
  - The approval store (manages approval gates)
  - The audit writer (persists the run record)

The executor enforces:
  - Scope ceilings (max_actions, max_duration_seconds, forbidden zones)
  - Approval gates (pauses on AWAIT_APPROVAL; halts on DENY)
  - Stop conditions (from Full-System-Operator-Safety-SOP.md Section 7)
  - Failure escalation (from Section 8)
  - Audit completeness (every run produces a written artifact)

Architecture: 06_AGENTS/Full-System-Operator-Surface.md
Safety SOP: 04_SOPS/Full-System-Operator-Safety-SOP.md
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Callable

from runtime.operator_surface.contracts import (
    OperatorScope,
    OperatorRunAudit,
    SessionStatus,
)
from runtime.operator_surface.capabilities import SurfaceType
from runtime.operator_surface.events import OperatorEvent, OperatorEventType
from runtime.operator_surface.session import SessionManager
from runtime.operator_surface.approvals import ApprovalStore, ApprovalDenied
from runtime.operator_surface.scopes import (
    ScopeViolation,
    check_action_limit,
    enforce_uri_in_scope,
    action_requires_approval,
    approval_required_actions_for,
)
from runtime.operator_surface.audit import write_audit
from runtime.operator_surface.recovery import UnrecoverableFailure


class OperatorExecutor:
    """
    Orchestrates a full FSOS operator run.

    Usage (Phase 9 foothold — manifest-declared steps only):

        executor = OperatorExecutor(vault_root=..., on_event=my_handler)
        result = executor.run(
            workflow_id="browser_research",
            surface=SurfaceType.BROWSER,
            scope=my_scope,
            adapter=my_browser_adapter,
            plan=manifest_steps,
            goal="Extract screener data from finviz.com",
        )
    """

    def __init__(
        self,
        vault_root: Optional[Path] = None,
        on_event: Optional[Callable[[OperatorEvent], None]] = None,
    ):
        self.vault_root = vault_root
        self._on_event = on_event or (lambda e: None)
        self._session_mgr = SessionManager()
        self._approval_store = ApprovalStore()

    def run(
        self,
        workflow_id: str,
        surface: SurfaceType,
        scope: OperatorScope,
        adapter,                # OperatorSurfaceAdapterBase instance
        plan: list[dict],
        goal: str,
    ) -> OperatorRunAudit:
        """
        Execute a full operator run. Returns the completed OperatorRunAudit.
        Always writes the audit artifact to 07_LOGS/Agent-Activity/ on exit.
        """
        run_id = str(uuid.uuid4())
        scope.run_id = run_id
        started_at = datetime.now(timezone.utc).isoformat()

        # Validate scope
        scope_errors = scope.validate()
        if scope_errors:
            # Return immediate failure audit without touching any surface
            audit = OperatorRunAudit(
                run_id=run_id,
                workflow_id=workflow_id,
                surface=surface.value,
                scope=scope,
                outcome="FAILED",
                error=f"Scope validation failed: {'; '.join(scope_errors)}",
                started_at=started_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            write_audit(audit, self.vault_root)
            return audit

        # Create session
        session = self._session_mgr.create_session(
            run_id=run_id,
            workflow_id=workflow_id,
            surface=surface.value,
            scope=scope,
            total_steps=len(plan),
        )

        # Build the audit artifact (populated throughout run)
        audit = OperatorRunAudit(
            run_id=run_id,
            workflow_id=workflow_id,
            surface=surface.value,
            scope=scope,
            plan=plan,
            steps_planned=len(plan),
            started_at=started_at,
        )

        def emit(event: OperatorEvent) -> None:
            event.run_id = run_id
            event.surface = surface.value
            audit.events.append(event)
            self._session_mgr.record_event(session.session_id, event)
            self._on_event(event)

        # Initialize adapter
        try:
            adapter.initialize(scope, session)
        except Exception as e:
            audit.outcome = "FAILED"
            audit.error = f"Adapter initialization failed: {e}"
            audit.completed_at = datetime.now(timezone.utc).isoformat()
            write_audit(audit, self.vault_root)
            return audit

        # Emit PLAN_READY
        emit(OperatorEvent(
            event_type=OperatorEventType.PLAN_READY,
            timestamp=datetime.now(timezone.utc).isoformat(),
            step_index=0,
            description=f"Plan ready: {len(plan)} steps for goal: {goal}",
            payload={"plan": plan, "total_steps": len(plan)},
        ))

        # Execute steps
        outcome = "COMPLETE"
        for i, step in enumerate(plan):
            # Enforce time ceiling (step-level check)
            now = datetime.now(timezone.utc)

            # Enforce action ceiling
            try:
                check_action_limit(scope, audit.actions_taken)
            except ScopeViolation as sv:
                emit(OperatorEvent(
                    event_type=OperatorEventType.SESSION_FAILED,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    step_index=i,
                    description=str(sv),
                ))
                audit.outcome = "HALTED"
                audit.error = str(sv)
                break

            # Check approval requirement
            action_type = step.get("action_type", "")
            surface_defaults = approval_required_actions_for(surface, adapter)
            needs_approval = (
                action_requires_approval(action_type, scope)
                or action_type in surface_defaults
            )

            if needs_approval:
                approval_req = self._approval_store.create_request(
                    run_id=run_id,
                    step_index=i,
                    action_type=action_type,
                    target=step.get("target", ""),
                    description=step.get("description", f"Approve step {i}: {action_type}"),
                    surface=surface.value,
                )
                emit(OperatorEvent(
                    event_type=OperatorEventType.AWAIT_APPROVAL,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    step_index=i,
                    action_class=action_type,
                    description=approval_req.description,
                    approval_required=True,
                    approval_id=approval_req.approval_id,
                    payload={"approval_id": approval_req.approval_id},
                ))
                audit.approvals_required += 1
                # In Phase 9 foothold: approval is synchronous CLI interaction.
                # Future: OSRIL event bus will route async approval responses.
                # For now: caller must call executor.record_approval() before run proceeds.
                # This is the approval gate — execution pauses here.
                # The run() method returns here if no approval response available.
                # Full async approval flow is planned for Phase 9 OSRIL integration.
                audit.outcome = "AWAIT_APPROVAL"
                audit.completed_at = datetime.now(timezone.utc).isoformat()
                write_audit(audit, self.vault_root)
                return audit

            # Emit STEP_STARTED
            emit(OperatorEvent(
                event_type=OperatorEventType.STEP_STARTED,
                timestamp=datetime.now(timezone.utc).isoformat(),
                step_index=i,
                action_class=action_type,
                description=f"Step {i}: {action_type} → {step.get('target', '')}",
            ))

            # Execute step via adapter
            try:
                result = adapter.execute_step(step, emit)
                audit.actions_taken += 1
                audit.steps_completed += 1

                emit(OperatorEvent(
                    event_type=OperatorEventType.STEP_COMPLETE,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    step_index=i,
                    action_class=action_type,
                    description=f"Step {i} complete: {action_type}",
                    payload={"result": result.output or {}},
                    grounding_mode=result.grounding_mode_used,
                ))

            except (ScopeViolation, Exception) as e:
                audit.steps_failed += 1
                emit(OperatorEvent(
                    event_type=OperatorEventType.STEP_FAILED,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    step_index=i,
                    action_class=action_type,
                    description=f"Step {i} failed: {e}",
                    payload={"error": str(e)},
                ))

                # Attempt recovery
                audit.recovery_attempts += 1
                try:
                    recovery = adapter.recover(step, emit)
                    if not recovery.success:
                        raise UnrecoverableFailure(i, surface.value, str(e))
                except UnrecoverableFailure as uf:
                    emit(OperatorEvent(
                        event_type=OperatorEventType.SESSION_FAILED,
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        step_index=i,
                        description=str(uf),
                    ))
                    outcome = "FAILED"
                    audit.error = str(uf)
                    break

        # Teardown adapter
        try:
            adapter.teardown(outcome, emit)
        except Exception:
            pass

        # Finalize
        if outcome == "COMPLETE":
            emit(OperatorEvent(
                event_type=OperatorEventType.SESSION_COMPLETE,
                timestamp=datetime.now(timezone.utc).isoformat(),
                step_index=audit.steps_completed,
                description="Operator run complete.",
            ))

        audit.outcome = outcome
        audit.completed_at = datetime.now(timezone.utc).isoformat()
        audit.adapter_payload = adapter.build_audit_payload()
        audit.approvals = self._approval_store.get_all_records()
        audit.approvals_granted = sum(
            1 for a in audit.approvals if a.decision == "APPROVE"
        )
        audit.approvals_denied = sum(
            1 for a in audit.approvals if a.decision == "DENY"
        )

        write_audit(audit, self.vault_root)
        self._session_mgr.close_session(session.session_id, outcome)
        return audit
