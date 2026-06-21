"""Visibility-only runtime adapter event emitter for ChaseOS Chat.

This module is intentionally transport-small: it validates a runtime adapter's
machine-readable event contract and appends safe structured event records to a
repo-local JSONL spool. Emitting an event never grants execution authority.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.schedules.loader import _load_yaml_mapping

SCHEMA_VERSION = "runtime-adapter-event.v1"
DEFAULT_SPOOL_PATH = "07_LOGS/Runtime-Events/runtime-events.jsonl"
EVENT_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
_SECRET_KEY_RE = re.compile(r"(api[_-]?key|token|secret|password|passwd|pwd|seed[_-]?phrase|private[_-]?key|oauth)", re.I)
_SECRET_VALUE_RE = re.compile(
    r"(?i)(sk-[A-Za-z0-9_\-]{6,}|ghp_[A-Za-z0-9_]{6,}|xox[baprs]-[A-Za-z0-9_\-]{6,}|token\s*=\s*[^\s,;]+|api[_-]?key\s*=\s*[^\s,;]+)"
)
_HIDDEN_REASONING_KEYS = {
    "chain_of_thought",
    "hidden_chain_of_thought",
    "hidden_reasoning",
    "private_reasoning",
    "reasoning_trace",
    "model_reasoning",
    "thoughts",
}
_ALLOWED_TRANSPORTS = {"jsonl_spool"}


class RuntimeEventEmissionError(RuntimeError):
    """Fail-closed runtime event emission error."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _adapter_policy_path(vault_root: Path, adapter_id: str) -> Path:
    return vault_root / "runtime" / "policy" / "adapters" / f"{adapter_id}.yaml"


def load_adapter_event_contract(vault_root: str | Path, adapter_id: str) -> dict[str, Any]:
    """Load the runtime_event_contract block for an adapter manifest."""
    root = Path(vault_root)
    adapter = str(adapter_id or "").strip().lower()
    if not adapter:
        raise RuntimeEventEmissionError("adapter_id is required")
    path = _adapter_policy_path(root, adapter)
    if not path.exists():
        raise RuntimeEventEmissionError(f"adapter manifest not found: {path.relative_to(root)}")
    try:
        manifest = _load_yaml_mapping(path)
    except Exception as exc:  # noqa: BLE001 - normalize loader failures
        raise RuntimeEventEmissionError(f"adapter manifest parse failed: {exc}") from exc
    contract = manifest.get("runtime_event_contract")
    if not isinstance(contract, dict):
        raise RuntimeEventEmissionError(f"adapter {adapter!r} has no runtime_event_contract")
    return contract


def _contains_hidden_reasoning(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).strip().lower() in _HIDDEN_REASONING_KEYS:
                return True
            if _contains_hidden_reasoning(item):
                return True
    elif isinstance(value, list):
        return any(_contains_hidden_reasoning(item) for item in value)
    return False


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if _SECRET_KEY_RE.search(str(key)):
                redacted[str(key)] = "[REDACTED]"
            else:
                redacted[str(key)] = _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return _SECRET_VALUE_RE.sub("[REDACTED]", value)
    return value


def _safe_spool_path(vault_root: Path, configured_path: str | None) -> Path:
    rel = str(configured_path or DEFAULT_SPOOL_PATH).replace("\\", "/").lstrip("/")
    path = (vault_root / rel).resolve()
    root = vault_root.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise RuntimeEventEmissionError("event spool path must remain inside the vault root") from exc
    if not rel.startswith("07_LOGS/Runtime-Events/"):
        raise RuntimeEventEmissionError("event spool path must be under 07_LOGS/Runtime-Events/")
    if path.suffix != ".jsonl":
        raise RuntimeEventEmissionError("event spool path must be a .jsonl file")
    return path


def _validate_contract(contract: dict[str, Any], event_type: str) -> None:
    if contract.get("can_emit_events") is not True:
        raise RuntimeEventEmissionError("adapter contract does not allow event emission")
    transport = str(contract.get("event_transport") or "")
    if transport not in _ALLOWED_TRANSPORTS:
        raise RuntimeEventEmissionError(f"unsupported event transport: {transport!r}")
    allowed = {str(item) for item in (contract.get("allowed_event_types") or [])}
    denied = {str(item) for item in (contract.get("denied_event_types") or [])}
    if event_type in denied:
        raise RuntimeEventEmissionError(f"event_type {event_type!r} is explicitly denied")
    if event_type not in allowed:
        raise RuntimeEventEmissionError(f"event_type {event_type!r} is not allowed by adapter contract")


def _validate_event_inputs(adapter_id: str, runtime_name: str, runtime_type: str, event_type: str, summary: str) -> None:
    if not adapter_id:
        raise RuntimeEventEmissionError("adapter_id is required")
    if not runtime_name:
        raise RuntimeEventEmissionError("runtime_name is required")
    if not runtime_type:
        raise RuntimeEventEmissionError("runtime_type is required")
    if not event_type or not EVENT_TYPE_RE.match(event_type):
        raise RuntimeEventEmissionError("event_type must be dotted lowercase")
    if not str(summary or "").strip():
        raise RuntimeEventEmissionError("summary is required")


def emit_runtime_event(
    vault_root: str | Path,
    *,
    adapter_id: str,
    runtime_name: str,
    runtime_type: str,
    event_type: str,
    summary: str,
    run_id: str | None = None,
    session_id: str | None = None,
    status: str = "observed",
    severity: str = "info",
    payload: dict[str, Any] | None = None,
    artifact_refs: list[str] | None = None,
    approval_request_id: str | None = None,
    parent_event_id: str | None = None,
    actor_id: str | None = None,
    actor_type: str = "runtime",
) -> dict[str, Any]:
    """Validate, redact, and append one structured runtime event to the spool.

    The function fails closed: malformed events, unknown event types, disabled
    adapter contracts, hidden reasoning fields, unsafe spool paths, and unknown
    transports all raise RuntimeEventEmissionError before anything is written.
    """
    root = Path(vault_root)
    adapter = str(adapter_id or "").strip().lower()
    runtime = str(runtime_name or "").strip()
    rtype = str(runtime_type or "").strip()
    etype = str(event_type or "").strip()
    text = str(summary or "").strip()
    _validate_event_inputs(adapter, runtime, rtype, etype, text)

    event_payload = dict(payload or {})
    if _contains_hidden_reasoning(event_payload):
        raise RuntimeEventEmissionError("hidden reasoning fields are not allowed in runtime events")

    contract = load_adapter_event_contract(root, adapter)
    _validate_contract(contract, etype)
    spool_path = _safe_spool_path(root, str(contract.get("event_endpoint_or_spool_path") or DEFAULT_SPOOL_PATH))

    event = {
        "schema_version": SCHEMA_VERSION,
        "id": f"rte-{uuid.uuid4().hex}",
        "adapter_id": adapter,
        "runtime_name": runtime,
        "runtime_type": rtype,
        "event_type": etype,
        "severity": severity,
        "status": status,
        "summary": _redact(text),
        "payload": _redact(event_payload),
        "artifact_refs": [str(item) for item in (artifact_refs or [])],
        "approval_request_id": approval_request_id or "",
        "parent_event_id": parent_event_id or "",
        "actor_id": actor_id or adapter,
        "actor_type": actor_type,
        "run_id": run_id or "",
        "session_id": session_id or "",
        "created_at": _utc_now(),
        "event_transport": "jsonl_spool",
        "event_endpoint_or_spool_path": str(contract.get("event_endpoint_or_spool_path") or DEFAULT_SPOOL_PATH),
        "redaction_state": "redacted",
        "redaction_policy": str(contract.get("redaction_policy") or "redact-secret-like-values-and-sensitive-keys"),
        "chain_of_thought_policy": "excluded",
        "secret_handling_policy": str(contract.get("secret_handling_policy") or "redact-or-reject-never-emit-secrets"),
        "audit_required": bool(contract.get("audit_required", True)),
        "authority": {
            "visibility_event_only": True,
            "execution_authority_granted": False,
            "canonical_mutation_allowed": False,
            "provider_call_performed": False,
            "connector_call_performed": False,
            "shell_command_performed": False,
            "approval_consumed": False,
        },
    }
    # JSON serialization is part of validation: fail before writing if payload is
    # not serializable after redaction.
    line = json.dumps(event, sort_keys=True)
    spool_path.parent.mkdir(parents=True, exist_ok=True)
    with spool_path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    return event


def emit_file_read_event(
    vault_root: str | Path,
    *,
    adapter_id: str,
    runtime_name: str,
    runtime_type: str,
    path: str,
    summary: str | None = None,
    read_scope: str | None = None,
    payload: dict[str, Any] | None = None,
    **event_kwargs: Any,
) -> dict[str, Any]:
    """Emit a visibility-only file-read record for Graph/Chat runtime activity."""
    body = dict(payload or {})
    body["path"] = str(path)
    body["action"] = "read"
    if read_scope is not None:
        body["read_scope"] = str(read_scope)
    return emit_runtime_event(
        vault_root,
        adapter_id=adapter_id,
        runtime_name=runtime_name,
        runtime_type=runtime_type,
        event_type="file.read",
        summary=summary or f"{runtime_name} read a file.",
        payload=body,
        **event_kwargs,
    )


def emit_file_written_event(
    vault_root: str | Path,
    *,
    adapter_id: str,
    runtime_name: str,
    runtime_type: str,
    path: str,
    summary: str | None = None,
    write_scope: str | None = None,
    payload: dict[str, Any] | None = None,
    **event_kwargs: Any,
) -> dict[str, Any]:
    """Emit a visibility-only file-write record for Graph/Chat runtime activity."""
    body = dict(payload or {})
    body["path"] = str(path)
    body["action"] = "write"
    if write_scope is not None:
        body["write_scope"] = str(write_scope)
    return emit_runtime_event(
        vault_root,
        adapter_id=adapter_id,
        runtime_name=runtime_name,
        runtime_type=runtime_type,
        event_type="file.written",
        summary=summary or f"{runtime_name} wrote a file.",
        payload=body,
        **event_kwargs,
    )


def emit_artifact_created_event(
    vault_root: str | Path,
    *,
    adapter_id: str,
    runtime_name: str,
    runtime_type: str,
    artifact_path: str,
    summary: str | None = None,
    artifact_kind: str | None = None,
    payload: dict[str, Any] | None = None,
    artifact_refs: list[str] | None = None,
    **event_kwargs: Any,
) -> dict[str, Any]:
    """Emit a visibility-only artifact-created record for Graph runtime overlays."""
    body = dict(payload or {})
    body["artifact_path"] = str(artifact_path)
    if artifact_kind is not None:
        body["artifact_kind"] = str(artifact_kind)
    refs = list(artifact_refs or [])
    if artifact_path not in refs:
        refs.append(str(artifact_path))
    return emit_runtime_event(
        vault_root,
        adapter_id=adapter_id,
        runtime_name=runtime_name,
        runtime_type=runtime_type,
        event_type="artifact.created",
        summary=summary or f"{runtime_name} created an artifact.",
        payload=body,
        artifact_refs=refs,
        **event_kwargs,
    )
