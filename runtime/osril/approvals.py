from __future__ import annotations

import json
import os
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .contract import OSRILEvent, OSRILEventType
from .inspector import list_events
from .session import append_event


VALID_DECISIONS = ("APPROVE", "DENY")
_SAFE_APPROVAL_ID = re.compile(r"^[A-Za-z0-9_.-]+$")


class ApprovalResponseError(ValueError):
    """Raised when an OSRIL approval response cannot be recorded safely."""


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _approvals_dir(vault_root: Path) -> Path:
    return vault_root / "runtime" / "osril" / "approvals"


def _validate_approval_id(approval_id: str) -> str:
    normalized = str(approval_id or "").strip()
    if not normalized:
        raise ApprovalResponseError("approval_id is required")
    if not _SAFE_APPROVAL_ID.fullmatch(normalized):
        raise ApprovalResponseError(
            "approval_id may only contain letters, numbers, underscore, dash, and dot"
        )
    return normalized


def _response_path(vault_root: Path, approval_id: str) -> Path:
    safe_id = _validate_approval_id(approval_id)
    return _approvals_dir(vault_root) / f"{safe_id}.response.json"


def _application_path(vault_root: Path, approval_id: str) -> Path:
    safe_id = _validate_approval_id(approval_id)
    return _approvals_dir(vault_root) / f"{safe_id}.application.json"


def _resume_path(vault_root: Path, approval_id: str) -> Path:
    safe_id = _validate_approval_id(approval_id)
    return _approvals_dir(vault_root) / f"{safe_id}.resume.json"


def _normalize_decision(decision: str) -> str:
    normalized = str(decision or "").strip().upper()
    if normalized not in VALID_DECISIONS:
        raise ApprovalResponseError("decision must be APPROVE or DENY")
    return normalized


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ApprovalResponseError(f"invalid approval response JSON at {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ApprovalResponseError(f"approval response did not contain an object: {path}")
    return data


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp_path, path)


def read_approval_application(vault_root: Path, approval_id: str) -> dict[str, Any] | None:
    path = _application_path(vault_root, approval_id)
    if not path.exists():
        return None
    payload = _read_json(path)
    payload.setdefault("application_path", str(path))
    return payload


def read_approval_resume(vault_root: Path, approval_id: str) -> dict[str, Any] | None:
    path = _resume_path(vault_root, approval_id)
    if not path.exists():
        return None
    payload = _read_json(path)
    payload.setdefault("resume_path", str(path))
    return payload


def _overlay_application_state(
    vault_root: Path,
    response_payload: dict[str, Any],
) -> dict[str, Any]:
    payload = dict(response_payload)
    approval_id = str(payload.get("approval_id") or "")
    application = read_approval_application(vault_root, approval_id) if approval_id else None
    if application is None:
        payload.setdefault("applied_to_execution", False)
        return payload

    payload["applied_to_execution"] = True
    payload["application_id"] = application.get("application_id")
    payload["application_kind"] = application.get("application_kind")
    payload["application_status"] = application.get("application_status")
    payload["application_path"] = application.get("application_path")
    payload["applied_at"] = application.get("applied_at")
    payload["applied_event_id"] = application.get("applied_event_id")
    resume = read_approval_resume(vault_root, approval_id)
    if resume is not None:
        payload["resume_executed"] = True
        payload["resume_id"] = resume.get("resume_id")
        payload["resume_kind"] = resume.get("resume_kind")
        payload["resume_status"] = resume.get("resume_status")
        payload["resume_path"] = resume.get("resume_path")
        payload["resumed_at"] = resume.get("resumed_at")
        payload["resumed_session_id"] = resume.get("resumed_session_id")
        payload["resumed_run_id"] = resume.get("resumed_run_id")
    else:
        payload["resume_executed"] = bool(application.get("resume_executed", False))
    return payload


def read_approval_response(vault_root: Path, approval_id: str) -> dict[str, Any] | None:
    path = _response_path(vault_root, approval_id)
    if not path.exists():
        return None
    payload = _read_json(path)
    payload.setdefault("response_path", str(path))
    return _overlay_application_state(vault_root, payload)


def list_approval_responses(
    vault_root: Path,
    *,
    runtime_id: str | None = None,
    workflow_id: str | None = None,
    session_id: str | None = None,
    decision: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    approvals_dir = _approvals_dir(vault_root)
    if not approvals_dir.exists():
        return []

    normalized_decision = _normalize_decision(decision) if decision else None
    responses: list[dict[str, Any]] = []
    for path in sorted(approvals_dir.glob("*.response.json")):
        item = _read_json(path)
        if runtime_id and item.get("runtime_id") != runtime_id:
            continue
        if workflow_id and item.get("workflow_id") != workflow_id:
            continue
        if session_id and item.get("session_id") != session_id:
            continue
        if normalized_decision and item.get("decision") != normalized_decision:
            continue
        item["response_path"] = str(path)
        responses.append(_overlay_application_state(vault_root, item))

    responses.sort(key=lambda item: str(item.get("responded_at") or ""), reverse=True)
    if limit is not None:
        responses = responses[: max(0, int(limit))]
    return responses


def _approval_required_events(
    vault_root: Path,
    *,
    runtime_id: str | None = None,
    workflow_id: str | None = None,
    session_id: str | None = None,
    approval_id: str | None = None,
) -> list[dict[str, Any]]:
    payload = list_events(
        vault_root,
        session_id=session_id,
        runtime_id=runtime_id,
        workflow_id=workflow_id,
        event_type="approval_required",
        limit=None,
    )
    events: list[dict[str, Any]] = []
    for event in payload.get("events", []):
        event_approval_id = str((event.get("payload") or {}).get("approval_id") or "")
        if approval_id and event_approval_id != approval_id:
            continue
        if event_approval_id:
            events.append(event)
    return events


def find_pending_approvals(
    vault_root: Path,
    *,
    runtime_id: str | None = None,
    workflow_id: str | None = None,
    session_id: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    events = _approval_required_events(
        vault_root,
        runtime_id=runtime_id,
        workflow_id=workflow_id,
        session_id=session_id,
    )
    pending: list[dict[str, Any]] = []
    for event in events:
        approval_id = str((event.get("payload") or {}).get("approval_id") or "")
        if read_approval_response(vault_root, approval_id) is not None:
            continue
        pending.append(
            {
                "approval_id": approval_id,
                "session_id": event.get("session_id"),
                "run_id": event.get("run_id"),
                "runtime_id": event.get("runtime_id"),
                "workflow_id": event.get("workflow_id"),
                "event_id": event.get("event_id"),
                "requested_at": event.get("timestamp"),
                "state": event.get("state"),
                "payload": event.get("payload") or {},
            }
        )

    pending.sort(key=lambda item: str(item.get("requested_at") or ""), reverse=True)
    if limit is not None:
        pending = pending[: max(0, int(limit))]
    return pending


def get_approval_state(
    vault_root: Path,
    *,
    runtime_id: str | None = None,
    workflow_id: str | None = None,
    session_id: str | None = None,
    decision: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    pending = find_pending_approvals(
        vault_root,
        runtime_id=runtime_id,
        workflow_id=workflow_id,
        session_id=session_id,
        limit=limit,
    )
    responses = list_approval_responses(
        vault_root,
        runtime_id=runtime_id,
        workflow_id=workflow_id,
        session_id=session_id,
        decision=decision,
        limit=limit,
    )
    return {
        "pending_count": len(pending),
        "pending": pending,
        "responses_count": len(responses),
        "responses": responses,
        "filters": {
            "runtime_id": runtime_id,
            "workflow_id": workflow_id,
            "session_id": session_id,
            "decision": decision,
            "limit": limit,
        },
    }


def record_approval_response(
    vault_root: Path,
    *,
    approval_id: str,
    decision: str,
    operator_id: str,
    operator_note: str | None = None,
    runtime_id: str | None = None,
    workflow_id: str | None = None,
    session_id: str | None = None,
    responded_at: str | None = None,
) -> dict[str, Any]:
    safe_approval_id = _validate_approval_id(approval_id)
    normalized_decision = _normalize_decision(decision)
    response_path = _response_path(vault_root, safe_approval_id)
    if response_path.exists():
        raise ApprovalResponseError(f"approval response already exists: {safe_approval_id}")

    matches = _approval_required_events(
        vault_root,
        runtime_id=runtime_id,
        workflow_id=workflow_id,
        session_id=session_id,
        approval_id=safe_approval_id,
    )
    if not matches:
        raise ApprovalResponseError(
            f"no matching pending approval_required event found for approval_id: {safe_approval_id}"
        )
    if len(matches) > 1:
        raise ApprovalResponseError(
            f"multiple approval_required events match approval_id '{safe_approval_id}'; add --session, --runtime, or --workflow"
        )

    source_event = matches[0]
    payload = {
        "schema_version": 1,
        "response_id": str(uuid.uuid4()),
        "approval_id": safe_approval_id,
        "decision": normalized_decision,
        "operator_id": str(operator_id or "operator").strip() or "operator",
        "operator_note": operator_note or "",
        "responded_at": responded_at or _now_iso(),
        "session_id": source_event.get("session_id"),
        "run_id": source_event.get("run_id"),
        "runtime_id": source_event.get("runtime_id"),
        "workflow_id": source_event.get("workflow_id"),
        "source_event_id": source_event.get("event_id"),
        "source_event_timestamp": source_event.get("timestamp"),
        "source_event_payload": source_event.get("payload") or {},
        "response_path": str(response_path),
        "applied_to_execution": False,
    }
    _write_json_atomic(response_path, payload)
    return apply_approval_response(vault_root, approval_id=safe_approval_id)


def apply_approval_response(
    vault_root: Path,
    *,
    approval_id: str,
    applied_at: str | None = None,
) -> dict[str, Any]:
    safe_approval_id = _validate_approval_id(approval_id)
    response_path = _response_path(vault_root, safe_approval_id)
    if not response_path.exists():
        raise ApprovalResponseError(f"approval response not found: {safe_approval_id}")

    response = _read_json(response_path)
    response.setdefault("response_path", str(response_path))
    existing = read_approval_application(vault_root, safe_approval_id)
    if existing is not None:
        return _overlay_application_state(vault_root, response)

    decision = _normalize_decision(str(response.get("decision") or ""))
    session_id = str(response.get("session_id") or "")
    run_id = str(response.get("run_id") or "")
    runtime_id = str(response.get("runtime_id") or "")
    workflow_id = str(response.get("workflow_id") or "")
    if not all([session_id, run_id, runtime_id, workflow_id]):
        raise ApprovalResponseError(
            f"approval response is missing execution linkage: {safe_approval_id}"
        )

    timestamp = applied_at or _now_iso()
    event = OSRILEvent(
        session_id=session_id,
        run_id=run_id,
        runtime_id=runtime_id,
        workflow_id=workflow_id,
        event_type=OSRILEventType.APPROVAL_RESPONSE,
        timestamp=timestamp,
        state="working" if decision == "APPROVE" else "halted",
        payload={
            "approval_id": safe_approval_id,
            "response_id": response.get("response_id"),
            "decision": decision,
            "operator_id": response.get("operator_id"),
            "operator_note": response.get("operator_note") or "",
            "source_event_id": response.get("source_event_id"),
            "application_kind": "osril_session_state",
            "resume_executed": False,
        },
    )
    append_event(vault_root, event)

    application_path = _application_path(vault_root, safe_approval_id)
    application = {
        "schema_version": 1,
        "application_id": str(uuid.uuid4()),
        "application_kind": "osril_session_state",
        "application_status": "recorded_in_osril_session",
        "approval_id": safe_approval_id,
        "response_id": response.get("response_id"),
        "decision": decision,
        "applied_at": timestamp,
        "applied_event_id": event.event_id,
        "session_id": session_id,
        "run_id": run_id,
        "runtime_id": runtime_id,
        "workflow_id": workflow_id,
        "application_path": str(application_path),
        "resume_executed": False,
    }
    _write_json_atomic(application_path, application)
    return _overlay_application_state(vault_root, response)


def mark_approval_resume(
    vault_root: Path,
    *,
    approval_id: str,
    resumed_session_id: str,
    resumed_run_id: str,
    workflow_id: str,
    runtime_id: str | None = None,
    resumed_at: str | None = None,
) -> dict[str, Any]:
    safe_approval_id = _validate_approval_id(approval_id)
    response = read_approval_response(vault_root, safe_approval_id)
    if response is None:
        raise ApprovalResponseError(f"approval response not found: {safe_approval_id}")
    if response.get("decision") != "APPROVE":
        raise ApprovalResponseError(
            f"approval response is not APPROVE: {safe_approval_id}"
        )
    if not response.get("applied_to_execution"):
        raise ApprovalResponseError(
            f"approval response has not been applied to OSRIL session state: {safe_approval_id}"
        )
    if response.get("resume_executed"):
        raise ApprovalResponseError(f"approval resume already exists: {safe_approval_id}")
    if response.get("workflow_id") != workflow_id:
        raise ApprovalResponseError(
            f"approval workflow mismatch: response is for {response.get('workflow_id')!r}, "
            f"not {workflow_id!r}"
        )

    resume_path = _resume_path(vault_root, safe_approval_id)
    if resume_path.exists():
        raise ApprovalResponseError(f"approval resume already exists: {safe_approval_id}")

    resume = {
        "schema_version": 1,
        "resume_id": str(uuid.uuid4()),
        "resume_kind": "aor_approval_gate",
        "resume_status": "executed",
        "approval_id": safe_approval_id,
        "response_id": response.get("response_id"),
        "decision": response.get("decision"),
        "operator_id": response.get("operator_id"),
        "resumed_at": resumed_at or _now_iso(),
        "session_id": response.get("session_id"),
        "run_id": response.get("run_id"),
        "runtime_id": runtime_id or response.get("runtime_id"),
        "workflow_id": workflow_id,
        "resumed_session_id": str(resumed_session_id or ""),
        "resumed_run_id": str(resumed_run_id or ""),
        "source_event_id": response.get("source_event_id"),
        "resume_path": str(resume_path),
    }
    _write_json_atomic(resume_path, resume)
    return resume
