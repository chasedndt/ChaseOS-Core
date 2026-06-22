from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from .contract import OSRILEvent, OSRILEventType


@dataclass
class OSRILSession:
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    run_id: str = ""
    runtime_id: str = ""
    workflow_id: str = ""
    status: str = "active"
    started_at: str = ""
    last_event_at: str = ""
    latest_event_type: Optional[str] = None
    event_count: int = 0
    event_log_path: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "OSRILSession":
        return cls(
            session_id=str(data.get("session_id") or str(uuid.uuid4())),
            run_id=str(data.get("run_id") or ""),
            runtime_id=str(data.get("runtime_id") or ""),
            workflow_id=str(data.get("workflow_id") or ""),
            status=str(data.get("status") or "active"),
            started_at=str(data.get("started_at") or ""),
            last_event_at=str(data.get("last_event_at") or ""),
            latest_event_type=data.get("latest_event_type"),
            event_count=int(data.get("event_count") or 0),
            event_log_path=str(data.get("event_log_path") or ""),
        )


def _run_dir(vault_root: Path) -> Path:
    path = vault_root / "runtime" / "osril" / "run"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _session_path(vault_root: Path, session_id: str) -> Path:
    return _run_dir(vault_root) / f"{session_id}.session.json"


def _events_path(vault_root: Path, session_id: str) -> Path:
    return _run_dir(vault_root) / f"{session_id}.events.jsonl"


def _status_for_event(event: OSRILEvent) -> str:
    if event.event_type == OSRILEventType.APPROVAL_REQUIRED:
        return "waiting_approval"
    if event.event_type == OSRILEventType.APPROVAL_RESPONSE:
        decision = str(event.payload.get("decision") or "").upper()
        return "halted" if decision == "DENY" else "active"
    if event.event_type == OSRILEventType.TASK_COMPLETE:
        return "complete"
    if event.event_type == OSRILEventType.TASK_FAILED:
        terminal = str(event.payload.get("terminal_status") or "failed")
        return "failed" if terminal == "failed" else "halted"
    return "active"


def create_session(
    vault_root: Path,
    run_id: str,
    runtime_id: str,
    workflow_id: str,
    timestamp: str | None = None,
    session_id: str | None = None,
) -> OSRILSession:
    session = OSRILSession(
        session_id=session_id or str(uuid.uuid4()),
        run_id=run_id,
        runtime_id=runtime_id,
        workflow_id=workflow_id,
        started_at=timestamp or "",
        last_event_at=timestamp or "",
    )
    session.event_log_path = str(_events_path(vault_root, session.session_id))
    write_session(vault_root, session)
    return session


def write_session(vault_root: Path, session: OSRILSession) -> None:
    _session_path(vault_root, session.session_id).write_text(
        json.dumps(session.to_dict(), indent=2),
        encoding="utf-8",
    )


def read_session(vault_root: Path, session_id: str) -> OSRILSession | None:
    path = _session_path(vault_root, session_id)
    if not path.exists():
        return None
    return OSRILSession.from_dict(json.loads(path.read_text(encoding="utf-8")))


def append_event(vault_root: Path, event: OSRILEvent) -> OSRILSession:
    errors = event.validate()
    if errors:
        raise ValueError("invalid OSRIL event: " + "; ".join(errors))
    session = read_session(vault_root, event.session_id)
    if session is None:
        session = create_session(
            vault_root=vault_root,
            run_id=event.run_id,
            runtime_id=event.runtime_id,
            workflow_id=event.workflow_id,
            timestamp=event.timestamp,
            session_id=event.session_id,
        )
    event_path = _events_path(vault_root, event.session_id)
    with event_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event.to_dict()) + "\n")
    session.last_event_at = event.timestamp
    session.latest_event_type = event.event_type.value
    session.event_count += 1
    session.status = _status_for_event(event)
    session.event_log_path = str(event_path)
    write_session(vault_root, session)
    return session


def read_session_events(vault_root: Path, session_id: str) -> list[OSRILEvent]:
    path = _events_path(vault_root, session_id)
    if not path.exists():
        return []
    events: list[OSRILEvent] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            events.append(OSRILEvent.from_dict(json.loads(line)))
    return events
