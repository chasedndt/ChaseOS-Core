"""
runtime.operator_surface.session

Session lifecycle management for FSOS runs.
Sessions are runtime-local ephemeral state — NOT canonical memory.
Session state is NOT written to the vault.
If the runtime restarts, the session is reconstructed from the audit artifact.

Session model defined in: 06_AGENTS/Full-System-Operator-Surface.md Section 6.5
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from runtime.operator_surface.contracts import OperatorScope, OperatorSession, SessionStatus
from runtime.operator_surface.events import OperatorEvent, OperatorEventType


class SessionManager:
    """
    Manages the lifecycle of FSOS sessions within a single process.
    Each run gets one session. Sessions are keyed by session_id.

    This is NOT a persistent store. Session state lives in memory
    for the duration of the run. Persistence is via the audit artifact.
    """

    def __init__(self):
        self._sessions: dict[str, OperatorSession] = {}

    def create_session(
        self,
        run_id: str,
        workflow_id: str,
        surface: str,
        scope: OperatorScope,
        total_steps: int = 0,
    ) -> OperatorSession:
        """Create and register a new session for a run."""
        session = OperatorSession(
            session_id=str(uuid.uuid4()),
            run_id=run_id,
            workflow_id=workflow_id,
            surface=surface,
            scope=scope,
            status=SessionStatus.ACTIVE,
            current_step=0,
            total_steps=total_steps,
            started_at=datetime.now(timezone.utc).isoformat(),
            last_active=datetime.now(timezone.utc).isoformat(),
        )
        self._sessions[session.session_id] = session
        return session

    def get_session(self, session_id: str) -> Optional[OperatorSession]:
        return self._sessions.get(session_id)

    def get_by_run_id(self, run_id: str) -> Optional[OperatorSession]:
        for session in self._sessions.values():
            if session.run_id == run_id:
                return session
        return None

    def record_event(self, session_id: str, event: OperatorEvent) -> None:
        """Append an event to the session and update status."""
        session = self._sessions.get(session_id)
        if session is None:
            return
        session.events.append(event)
        session.last_active = datetime.now(timezone.utc).isoformat()

        # Update session status from event type
        if event.event_type == OperatorEventType.AWAIT_APPROVAL:
            if event.approval_id:
                session.pending_approvals.append(event.approval_id)
        elif event.event_type == OperatorEventType.APPROVAL_RECEIVED:
            if event.approval_id and event.approval_id in session.pending_approvals:
                session.pending_approvals.remove(event.approval_id)
        elif event.event_type == OperatorEventType.STEP_COMPLETE:
            session.current_step = event.step_index + 1
            session.actions_taken += 1
        elif event.event_type == OperatorEventType.SESSION_COMPLETE:
            session.status = SessionStatus.COMPLETE
        elif event.event_type == OperatorEventType.SESSION_FAILED:
            session.status = SessionStatus.FAILED

    def close_session(self, session_id: str, outcome: str) -> Optional[OperatorSession]:
        """Mark session as closed. Returns the closed session."""
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if outcome in ("COMPLETE", "DONE"):
            session.status = SessionStatus.COMPLETE
        else:
            session.status = SessionStatus.FAILED
        return session

    def list_active(self) -> list[OperatorSession]:
        return [s for s in self._sessions.values() if s.status == SessionStatus.ACTIVE]
