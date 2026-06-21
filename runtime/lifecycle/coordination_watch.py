"""ChaseOS runtime coordination-watch lifecycle foothold.

Loads machine-readable lifecycle records and runs the declared coordination bus
watch mode for a specific runtime.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from runtime.agent_bus import bus
from runtime.lifecycle.health_cli import LIFECYCLE_DIR, load_lifecycle_record

ROOT = Path(__file__).resolve().parents[2]


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def load_coordination_watch_config(runtime_id: str) -> dict[str, Any]:
    record = load_lifecycle_record(runtime_id)
    config = dict(record.get("coordination_watch") or {})
    if not config:
        raise ValueError(f"No coordination_watch record found for runtime: {runtime_id}")
    return config


def run_coordination_watch(
    runtime_id: str,
    *,
    once: bool = False,
    interval_seconds: int | None = None,
) -> dict[str, Any]:
    config = load_coordination_watch_config(runtime_id)
    if not _coerce_bool(config.get("enabled"), default=False):
        raise ValueError(f"coordination_watch disabled for runtime: {runtime_id}")

    runtime_name = str(config.get("runtime_name") or runtime_id)
    claim_next = _coerce_bool(config.get("claim_next"), default=False)
    stale_after = config.get("stale_after_seconds")
    stale_after_seconds = int(stale_after) if stale_after not in (None, "") else None

    if once:
        result = bus.watch_once(
            ROOT,
            runtime=runtime_name,
            claim_next=claim_next,
            stale_after_seconds=stale_after_seconds,
        )
        return {
            "runtime": runtime_name,
            "mode": "once",
            "result": result,
        }

    effective_interval = interval_seconds
    if effective_interval is None:
        configured = config.get("interval_seconds")
        if configured in (None, ""):
            raise ValueError(f"No interval_seconds configured for runtime: {runtime_id}")
        effective_interval = int(configured)

    bus.run_watch_loop(
        ROOT,
        runtime=runtime_name,
        interval_seconds=int(effective_interval),
        claim_next=claim_next,
        stale_after_seconds=stale_after_seconds,
    )
    return {
        "runtime": runtime_name,
        "mode": "loop",
        "interval_seconds": int(effective_interval),
        "claim_next": claim_next,
        "stale_after_seconds": stale_after_seconds,
    }
