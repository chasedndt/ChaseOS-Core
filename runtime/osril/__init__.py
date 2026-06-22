"""OSRIL runtime-side contract substrate."""

from .contract import OSRILEvent, OSRILEventType
from .approvals import (
    ApprovalResponseError,
    apply_approval_response,
    find_pending_approvals,
    get_approval_state,
    list_approval_responses,
    mark_approval_resume,
    read_approval_application,
    read_approval_response,
    read_approval_resume,
    record_approval_response,
)
from .inspector import get_session_detail, list_events, list_sessions
from .session import OSRILSession, append_event, create_session, read_session, read_session_events

__all__ = [
    "ApprovalResponseError",
    "OSRILEvent",
    "OSRILEventType",
    "OSRILSession",
    "apply_approval_response",
    "append_event",
    "create_session",
    "find_pending_approvals",
    "get_approval_state",
    "get_session_detail",
    "list_approval_responses",
    "mark_approval_resume",
    "list_events",
    "list_sessions",
    "read_approval_application",
    "read_approval_response",
    "read_approval_resume",
    "read_session",
    "read_session_events",
    "record_approval_response",
]
