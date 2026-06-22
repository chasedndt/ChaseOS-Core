"""SBP delivery health telemetry.

This ledger records external delivery outcomes such as Discord webhook and
Whop forum post success, credential/configuration failure, API failure, or
Gate-blocked delivery. It is separate from runtime provider-state fallback
governance.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "1.0"
DELIVERY_HEALTH_RELATIVE_PATH = Path("runtime/sbp/state/delivery_health_events.jsonl")
logger = logging.getLogger(__name__)
VALID_EVENT_TYPES = {
    "delivery.attempt_succeeded",
    "delivery.attempt_failed",
    "delivery.attempt_skipped",
    "delivery.draft_written",
}
VALID_STATUSES = {"succeeded", "failed", "skipped"}
_EVENT_STATUS = {
    "delivery.attempt_succeeded": "succeeded",
    "delivery.attempt_failed": "failed",
    "delivery.attempt_skipped": "skipped",
    "delivery.draft_written": "skipped",
}
_SECRET_PATTERNS = (
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]+", re.IGNORECASE), r"\1[redacted]"),
    (
        # Literals are split via adjacent-string concatenation so this redaction
        # module does not itself contain the contiguous webhook string it redacts
        # (keeps the Core export privacy scanner from matching its own pattern source).
        re.compile(r"https://(?:canary\.|ptb\.)?dis" r"cord(?:app)?\.com/api/web" r"hooks/[^\s)]+", re.IGNORECASE),
        "https://dis" "cord.com/api/web" "hooks/[redacted]",
    ),
    (re.compile(r"\b(?:whop|sk|pplx|xai)-[A-Za-z0-9._-]+\b", re.IGNORECASE), "[redacted]"),
)


class DeliveryHealthLedgerError(RuntimeError):
    """Raised when delivery health telemetry is invalid."""


@dataclass(frozen=True)
class DeliveryHealthEvent:
    event_type: str
    adapter_id: str
    surface: str
    pipeline_id: str
    provider: str | None = None
    status: str | None = None
    delivery_target: str | None = None
    channel_hint: str | None = None
    run_date: str | None = None
    failure_reason: str | None = None
    error_type: str | None = None
    error_preview: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: str | None = None
    event_id: str | None = None
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        event_type = _validate_event_type(self.event_type)
        status = _validate_status(self.status or _EVENT_STATUS[event_type])
        timestamp = self.timestamp or _utc_now()
        _parse_timestamp(timestamp, "timestamp")
        if not isinstance(self.data, dict):
            raise DeliveryHealthLedgerError("data must be an object")
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id or f"delivery-health-{uuid.uuid4().hex[:12]}",
            "timestamp": timestamp,
            "event_type": event_type,
            "adapter_id": _validate_non_empty(self.adapter_id, "adapter_id"),
            "provider": self.provider or self.adapter_id,
            "surface": _validate_non_empty(self.surface, "surface"),
            "pipeline_id": _validate_non_empty(self.pipeline_id, "pipeline_id"),
            "delivery_target": self.delivery_target,
            "channel_hint": self.channel_hint,
            "run_date": self.run_date,
            "status": status,
            "failure_reason": self.failure_reason,
            "error_type": self.error_type,
            "error_preview": self.error_preview,
            "data": dict(self.data),
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: Any, field_name: str) -> datetime:
    if not value:
        raise DeliveryHealthLedgerError(f"{field_name} is required")
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DeliveryHealthLedgerError(f"{field_name} is not ISO-8601: {text!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _validate_non_empty(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise DeliveryHealthLedgerError(f"{field_name} is required")
    return text


def _validate_event_type(event_type: Any) -> str:
    text = _validate_non_empty(event_type, "event_type")
    if text not in VALID_EVENT_TYPES:
        raise DeliveryHealthLedgerError(
            f"Unsupported delivery health event_type {text!r}; expected one of {sorted(VALID_EVENT_TYPES)}"
        )
    return text


def _validate_status(status: Any) -> str:
    text = _validate_non_empty(status, "status")
    if text not in VALID_STATUSES:
        raise DeliveryHealthLedgerError(f"Unsupported delivery health status {text!r}")
    return text


def delivery_health_path(vault_root: str | Path) -> Path:
    return Path(vault_root) / DELIVERY_HEALTH_RELATIVE_PATH


def safe_error_preview(error: BaseException | str, limit: int = 300) -> str:
    """Return a short error preview with common delivery secret shapes redacted."""
    text = str(error).replace("\n", " ").strip()
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text[:limit]


def classify_delivery_failure(error: BaseException | str) -> str:
    """Map delivery failures to stable, non-provider-governance reason codes."""
    name = type(error).__name__.lower()
    text = str(error).lower()
    if "gate blocked" in text or "policy denied" in text:
        return "gate_blocked"
    if "credential" in name or "env var not set" in text or "api key" in text or "webhook url" in text:
        return "credential_missing"
    if "channel_id not set" in text or "forum_experience_id" in text or "manifest" in text:
        return "configuration_missing"
    if "network" in text or "timeout" in text or "timed out" in text or "connection" in text:
        return "network_error"
    if "http" in name or "http" in text or "quota" in text or "forbidden" in text or "unauthorized" in text:
        return "api_error"
    return "unexpected_error"


def _validate_event_dict(
    data: dict[str, Any],
    *,
    path: Path | None = None,
    line_number: int | None = None,
) -> dict[str, Any]:
    location = ""
    if path is not None:
        location = f" at {path}"
        if line_number is not None:
            location += f":{line_number}"

    required = [
        "schema_version",
        "event_id",
        "timestamp",
        "event_type",
        "adapter_id",
        "surface",
        "pipeline_id",
        "status",
        "data",
    ]
    missing = [field_name for field_name in required if field_name not in data]
    if missing:
        raise DeliveryHealthLedgerError(f"Delivery health event{location} missing fields: {missing}")
    if str(data.get("schema_version")) != SCHEMA_VERSION:
        raise DeliveryHealthLedgerError(
            f"Delivery health event{location} has unsupported schema_version {data.get('schema_version')!r}"
        )
    _validate_non_empty(data.get("event_id"), "event_id")
    _parse_timestamp(data.get("timestamp"), "timestamp")
    event_type = _validate_event_type(data.get("event_type"))
    status = _validate_status(data.get("status"))
    if status != _EVENT_STATUS[event_type]:
        raise DeliveryHealthLedgerError(
            f"Delivery health event{location} status {status!r} does not match event_type {event_type!r}"
        )
    _validate_non_empty(data.get("adapter_id"), "adapter_id")
    _validate_non_empty(data.get("surface"), "surface")
    _validate_non_empty(data.get("pipeline_id"), "pipeline_id")
    if not isinstance(data.get("data"), dict):
        raise DeliveryHealthLedgerError(f"Delivery health event{location} data must be an object")
    return dict(data)


def append_delivery_health_event(vault_root: str | Path, event: DeliveryHealthEvent | dict[str, Any]) -> dict[str, Any]:
    """Append a validated delivery health event and return the persisted dict."""
    payload = event.to_dict() if isinstance(event, DeliveryHealthEvent) else dict(event)
    payload = _validate_event_dict(payload)
    path = delivery_health_path(vault_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")
    return payload


def record_delivery_health_event(
    vault_root: str | Path,
    *,
    event_type: str,
    adapter_id: str,
    surface: str,
    pipeline_id: str,
    provider: str | None = None,
    delivery_target: str | None = None,
    channel_hint: str | None = None,
    run_date: str | None = None,
    failure_reason: str | None = None,
    error: BaseException | str | None = None,
    data: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Record delivery telemetry without blocking fail-open delivery paths."""
    event = DeliveryHealthEvent(
        event_type=event_type,
        adapter_id=adapter_id,
        provider=provider or adapter_id,
        surface=surface,
        pipeline_id=pipeline_id,
        delivery_target=delivery_target,
        channel_hint=channel_hint,
        run_date=run_date,
        failure_reason=failure_reason,
        error_type=type(error).__name__ if isinstance(error, BaseException) else ("delivery_detail" if error else None),
        error_preview=safe_error_preview(error) if error is not None else None,
        data=data or {},
    )
    try:
        return append_delivery_health_event(vault_root, event)
    except Exception as exc:
        logger.warning("delivery health telemetry write failed (%s: %s)", type(exc).__name__, exc)
        return None


def load_delivery_health_events(
    vault_root: str | Path,
    *,
    adapter_id: str | None = None,
    status: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Load delivery health events in timestamp order."""
    path = delivery_health_path(vault_root)
    if not path.exists():
        return []

    if status is not None:
        _validate_status(status)

    events: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            text = line.strip()
            if not text:
                continue
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                raise DeliveryHealthLedgerError(f"Invalid delivery health JSONL at {path}:{line_number}: {exc}") from exc
            if not isinstance(data, dict):
                raise DeliveryHealthLedgerError(f"Delivery health event at {path}:{line_number} is not an object")
            event = _validate_event_dict(data, path=path, line_number=line_number)
            if adapter_id and str(event.get("adapter_id", "")).lower() != str(adapter_id).lower():
                continue
            if status and event.get("status") != status:
                continue
            events.append(event)

    events.sort(key=lambda item: item.get("timestamp", ""))
    if limit is not None and limit >= 0:
        return events[-int(limit):]
    return events


def summarize_delivery_health(
    vault_root: str | Path,
    *,
    adapter_id: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """Summarize recent delivery health events without affecting provider state."""
    path = delivery_health_path(vault_root)
    events = load_delivery_health_events(vault_root, adapter_id=adapter_id, limit=limit)
    status_counts = {status: 0 for status in sorted(VALID_STATUSES)}
    adapters: dict[str, dict[str, Any]] = {}
    for event in events:
        status = str(event.get("status") or "")
        if status in status_counts:
            status_counts[status] += 1
        adapter = str(event.get("adapter_id") or "unknown")
        current = adapters.setdefault(
            adapter,
            {
                "event_count": 0,
                "status_counts": {status_name: 0 for status_name in sorted(VALID_STATUSES)},
                "latest_event": None,
            },
        )
        current["event_count"] += 1
        if status in current["status_counts"]:
            current["status_counts"][status] += 1
        current["latest_event"] = _event_summary(event)

    return {
        "schema_version": 1,
        "path": str(DELIVERY_HEALTH_RELATIVE_PATH).replace("\\", "/"),
        "exists": path.exists(),
        "event_count": len(events),
        "status_counts": status_counts,
        "adapters": adapters,
    }


def _event_summary(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": event.get("event_id"),
        "timestamp": event.get("timestamp"),
        "event_type": event.get("event_type"),
        "adapter_id": event.get("adapter_id"),
        "provider": event.get("provider"),
        "surface": event.get("surface"),
        "pipeline_id": event.get("pipeline_id"),
        "delivery_target": event.get("delivery_target"),
        "channel_hint": event.get("channel_hint"),
        "run_date": event.get("run_date"),
        "status": event.get("status"),
        "failure_reason": event.get("failure_reason"),
        "error_type": event.get("error_type"),
        "data": event.get("data") or {},
    }
