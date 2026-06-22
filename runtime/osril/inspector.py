from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .contract import OSRILEvent
from .session import OSRILSession


def _osril_run_dir(vault_root: Path) -> Path:
    return vault_root / "runtime" / "osril" / "run"


def _session_path(vault_root: Path, session_id: str) -> Path:
    return _osril_run_dir(vault_root) / f"{session_id}.session.json"


def _events_path(vault_root: Path, session_id: str) -> Path:
    return _osril_run_dir(vault_root) / f"{session_id}.events.jsonl"


def _load_session_file(path: Path) -> OSRILSession:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid OSRIL session JSON at {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"OSRIL session file did not contain an object: {path}")
    return OSRILSession.from_dict(data)


def _event_to_dict(event: OSRILEvent) -> dict[str, Any]:
    return event.to_dict()


def _read_session(vault_root: Path, session_id: str) -> OSRILSession | None:
    path = _session_path(vault_root, session_id)
    if not path.exists():
        return None
    return _load_session_file(path)


def _read_session_events(vault_root: Path, session_id: str) -> list[OSRILEvent]:
    path = _events_path(vault_root, session_id)
    if not path.exists():
        return []
    events: list[OSRILEvent] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid OSRIL event JSON at {path}:{line_number}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"OSRIL event line did not contain an object: {path}:{line_number}")
        events.append(OSRILEvent.from_dict(payload))
    return events


def list_sessions(
    vault_root: Path,
    *,
    runtime_id: str | None = None,
    workflow_id: str | None = None,
    status: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    run_dir = _osril_run_dir(vault_root)
    if not run_dir.exists():
        return {
            "count": 0,
            "sessions": [],
            "filters": {
                "runtime_id": runtime_id,
                "workflow_id": workflow_id,
                "status": status,
                "limit": limit,
            },
        }
    sessions: list[dict[str, Any]] = []
    for path in sorted(run_dir.glob("*.session.json")):
        session = _load_session_file(path)
        if runtime_id and session.runtime_id != runtime_id:
            continue
        if workflow_id and session.workflow_id != workflow_id:
            continue
        if status and session.status != status:
            continue
        item = session.to_dict()
        item["session_path"] = str(path)
        sessions.append(item)

    sessions.sort(key=lambda item: str(item.get("last_event_at") or item.get("started_at") or ""), reverse=True)
    if limit is not None:
        sessions = sessions[: max(0, int(limit))]

    return {
        "count": len(sessions),
        "sessions": sessions,
        "filters": {
            "runtime_id": runtime_id,
            "workflow_id": workflow_id,
            "status": status,
            "limit": limit,
        },
    }


def get_session_detail(
    vault_root: Path,
    session_id: str,
    *,
    include_events: bool = True,
    event_limit: int | None = None,
) -> dict[str, Any]:
    session = _read_session(vault_root, session_id)
    if session is None:
        raise ValueError(f"OSRIL session not found: {session_id}")

    events = _read_session_events(vault_root, session_id) if include_events else []
    if event_limit is not None:
        events = events[-max(0, int(event_limit)) :]

    return {
        "session": session.to_dict(),
        "event_count": len(events),
        "events": [_event_to_dict(event) for event in events],
    }


def list_events(
    vault_root: Path,
    *,
    session_id: str | None = None,
    runtime_id: str | None = None,
    workflow_id: str | None = None,
    event_type: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    session_payload = list_sessions(
        vault_root,
        runtime_id=runtime_id,
        workflow_id=workflow_id,
        limit=None,
    )
    selected_sessions = session_payload["sessions"]
    if session_id:
        selected_sessions = [item for item in selected_sessions if item.get("session_id") == session_id]
        if not selected_sessions:
            raise ValueError(f"OSRIL session not found: {session_id}")

    events: list[dict[str, Any]] = []
    for item in selected_sessions:
        for event in _read_session_events(vault_root, str(item["session_id"])):
            event_dict = _event_to_dict(event)
            if event_type and event_dict.get("event_type") != event_type:
                continue
            events.append(event_dict)

    events.sort(key=lambda item: str(item.get("timestamp") or ""), reverse=True)
    if limit is not None:
        events = events[: max(0, int(limit))]

    return {
        "count": len(events),
        "events": events,
        "filters": {
            "session_id": session_id,
            "runtime_id": runtime_id,
            "workflow_id": workflow_id,
            "event_type": event_type,
            "limit": limit,
        },
    }
