"""
router.py — Capability-Aware Task Router for the ChaseOS Agent Bus

Routes task types to the correct runtime based on:
  1. Capability manifests (runtime/{runtime}/capabilities.yaml) — which runtimes CAN handle it
  2. Heartbeat liveness (agent_bus SQLite) — which runtimes are NOT stale
  3. Concurrent load (max_concurrent_tasks) — which live runtimes have capacity

The router answers: "given this task_type, which runtime should handle it right now?"

This is the dispatch-time routing layer. It does NOT modify the bus — it only reads.
Actual claim/update operations go through bus.py as before.

Design:
  - Recommended = highest-priority eligible runtime that is live AND not at capacity
  - At-capacity runtimes (task count >= max_concurrent_tasks) are tracked but deprioritized
  - If all live runtimes are at capacity, recommended falls back to first live runtime
    (task may queue) rather than returning None
  - If all eligible runtimes are stale, recommended=None and reason explains why
  - Stale threshold comes from the runtime's own heartbeat_stale_seconds config
    (or a caller-supplied override)
  - N-runtime capable: adding a capabilities.yaml registers a new runtime automatically

Public API:
    RouteResult
    route_task_type(task_type, vault_root, *, stale_override_seconds=None) -> RouteResult
    get_stale_runtimes(vault_root, *, threshold_seconds=None) -> list[str]
    get_runtime_liveness(vault_root) -> dict[str, RuntimeLiveness]
    RouterError
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.agent_bus.capabilities import (
    load_all_capabilities,
    RuntimeCapabilities,
    CapabilityError,
)


class RouterError(Exception):
    """Raised when routing cannot be performed."""


# Priority rank mapping — mirrors bus.py _PRIORITY_RANKS for ceiling enforcement.
# lower number = lower priority; "critical" is highest.
_PRIORITY_RANKS: dict[str, int] = {"low": 0, "normal": 1, "high": 2, "critical": 3}


# ---------------------------------------------------------------------------
# Liveness
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RuntimeLiveness:
    runtime: str          # bus_name (e.g. "OpenClaw")
    runtime_name: str     # filesystem name (e.g. "openclaw")
    last_seen: str | None
    status: str | None
    health: str | None
    age_seconds: float | None
    is_stale: bool
    stale_threshold_seconds: int


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _read_heartbeats(vault_root: Path) -> dict[str, list[dict[str, Any]]]:
    """Read heartbeats table from the bus DB. Returns rows grouped by runtime bus_name."""
    from runtime.agent_bus.bus import db_path, init_db
    db = db_path(vault_root)
    if not db.exists():
        init_db(vault_root)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM heartbeats ORDER BY runtime ASC, last_seen DESC").fetchall()
    conn.close()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["runtime"], []).append(dict(row))
    return grouped


def _read_owned_task_counts(vault_root: Path) -> dict[str, int]:
    """
    Return count of active tasks per owner runtime (bus_name).
    Counts tasks in states: claimed, in_progress, blocked, review.
    Runtimes with no active tasks are not present in the result (default to 0).
    """
    from runtime.agent_bus.bus import db_path, init_db
    db = db_path(vault_root)
    if not db.exists():
        init_db(vault_root)
    try:
        conn = sqlite3.connect(db)
        rows = conn.execute(
            """
            SELECT owner, COUNT(*) AS cnt FROM tasks
            WHERE status IN ('claimed', 'in_progress', 'blocked', 'review')
              AND owner IS NOT NULL
            GROUP BY owner
            """
        ).fetchall()
        conn.close()
    except sqlite3.Error:
        return {}
    return {row[0]: row[1] for row in rows}


def get_runtime_liveness(
    vault_root: str | Path,
    *,
    stale_override_seconds: int | None = None,
) -> dict[str, RuntimeLiveness]:
    """
    Return liveness status for all registered runtimes.
    A runtime with no heartbeat entry is considered stale.
    Keyed by bus_name (e.g. "OpenClaw", "Hermes").
    """
    root = Path(vault_root)
    all_caps = load_all_capabilities(root)
    heartbeats = _read_heartbeats(root)
    now = _now_utc()

    result: dict[str, RuntimeLiveness] = {}
    for runtime_name, caps in all_caps.items():
        bus_name = caps.bus_name
        threshold = stale_override_seconds if stale_override_seconds is not None else caps.heartbeat_stale_seconds
        hb_rows = heartbeats.get(bus_name) or []

        if not hb_rows:
            result[bus_name] = RuntimeLiveness(
                runtime=bus_name,
                runtime_name=runtime_name,
                last_seen=None,
                status=None,
                health=None,
                age_seconds=None,
                is_stale=True,
                stale_threshold_seconds=threshold,
            )
            continue

        freshest: dict[str, Any] | None = None
        freshest_dt: datetime | None = None
        for hb in hb_rows:
            last_seen_str = hb.get("last_seen", "")
            try:
                candidate_dt = _parse_iso(last_seen_str)
            except (ValueError, TypeError):
                continue
            if freshest_dt is None or candidate_dt > freshest_dt:
                freshest_dt = candidate_dt
                freshest = hb

        if freshest is None:
            result[bus_name] = RuntimeLiveness(
                runtime=bus_name,
                runtime_name=runtime_name,
                last_seen=None,
                status=None,
                health=None,
                age_seconds=None,
                is_stale=True,
                stale_threshold_seconds=threshold,
            )
            continue

        last_seen_str = freshest.get("last_seen", "")
        age = (now - freshest_dt).total_seconds() if freshest_dt is not None else None
        is_stale = (age is None) or (age > threshold)
        result[bus_name] = RuntimeLiveness(
            runtime=bus_name,
            runtime_name=runtime_name,
            last_seen=last_seen_str or None,
            status=freshest.get("status"),
            health=freshest.get("health"),
            age_seconds=age,
            is_stale=is_stale,
            stale_threshold_seconds=threshold,
        )

    return result


def get_stale_runtimes(
    vault_root: str | Path,
    *,
    threshold_seconds: int | None = None,
) -> list[str]:
    """
    Return list of bus_names for runtimes whose heartbeat exceeds their stale threshold.
    Sorted alphabetically. A runtime with no heartbeat entry is always stale.
    """
    liveness = get_runtime_liveness(vault_root, stale_override_seconds=threshold_seconds)
    return sorted(name for name, live in liveness.items() if live.is_stale)


# ---------------------------------------------------------------------------
# Route result
# ---------------------------------------------------------------------------

@dataclass
class RouteResult:
    task_type: str
    eligible_runtimes: list[str]         # all capable runtimes (bus_names), priority order
    live_runtimes: list[str]             # eligible runtimes that are NOT stale
    stale_runtimes: list[str]            # eligible runtimes that ARE stale
    at_capacity_runtimes: list[str]      # live runtimes at max_concurrent_tasks ceiling
    available_runtimes: list[str]        # live runtimes with capacity remaining (recommended from here)
    all_registered: list[str]            # all bus_names registered in capabilities
    recommended: str | None             # best available runtime; None if all eligible are stale
    reason: str


# ---------------------------------------------------------------------------
# Public router
# ---------------------------------------------------------------------------

def route_task_type(
    task_type: str,
    vault_root: str | Path,
    *,
    stale_override_seconds: int | None = None,
) -> RouteResult:
    """
    Return a RouteResult for the given task_type.

    Recommended logic:
      1. Find all runtimes with this capability (capability manifests).
      2. Filter to those whose heartbeat is fresh (liveness check).
      3. Recommend the highest-priority fresh runtime.
      4. If all are stale, recommended=None and reason explains.
      5. If no runtime handles this task_type, recommended=None.

    Does NOT modify the bus. Call bus.claim_task() after routing.
    """
    root = Path(vault_root)

    try:
        all_caps = load_all_capabilities(root)
    except CapabilityError as exc:
        raise RouterError(f"Cannot load capability registry: {exc}") from exc

    all_registered = sorted(caps.bus_name for caps in all_caps.values())
    liveness = get_runtime_liveness(root, stale_override_seconds=stale_override_seconds)

    # Build eligible list in priority order
    eligible_with_rank: list[tuple[int, str]] = []
    for runtime_name, caps in all_caps.items():
        if caps.can_handle(task_type):
            rank = caps.priority_for(task_type)
            eligible_with_rank.append((rank, caps.bus_name))
    eligible_with_rank.sort(key=lambda x: (x[0], x[1]))
    eligible_runtimes = [bus_name for _, bus_name in eligible_with_rank]

    if not eligible_runtimes:
        return RouteResult(
            task_type=task_type,
            eligible_runtimes=[],
            live_runtimes=[],
            stale_runtimes=[],
            at_capacity_runtimes=[],
            available_runtimes=[],
            all_registered=all_registered,
            recommended=None,
            reason=f"No registered runtime declares capability to handle task_type '{task_type}'.",
        )

    live_runtimes: list[str] = []
    stale_runtimes: list[str] = []
    for bus_name in eligible_runtimes:
        live = liveness.get(bus_name)
        if live is None or live.is_stale:
            stale_runtimes.append(bus_name)
        else:
            live_runtimes.append(bus_name)

    # Concurrent load check: split live runtimes into available vs at-capacity
    owned_counts = _read_owned_task_counts(root)
    at_capacity_runtimes: list[str] = []
    available_runtimes: list[str] = []
    caps_by_bus: dict[str, RuntimeCapabilities] = {c.bus_name: c for c in all_caps.values()}
    for bus_name in live_runtimes:
        caps = caps_by_bus.get(bus_name)
        if caps is None:
            available_runtimes.append(bus_name)
            continue
        count = owned_counts.get(bus_name, 0)
        if count >= caps.max_concurrent_tasks:
            at_capacity_runtimes.append(bus_name)
        else:
            available_runtimes.append(bus_name)

    if available_runtimes:
        recommended = available_runtimes[0]
        notes: list[str] = []
        if stale_runtimes:
            notes.append(f"{len(stale_runtimes)} stale")
        if at_capacity_runtimes:
            notes.append(f"{len(at_capacity_runtimes)} at capacity")
        note_str = f" ({', '.join(notes)})" if notes else ""
        reason = (
            f"Recommended '{recommended}' — highest-priority live+available runtime "
            f"for '{task_type}'{note_str}."
        )
    elif live_runtimes:
        # All live runtimes are at capacity — recommend first live one anyway with warning
        recommended = live_runtimes[0]
        reason = (
            f"All {len(live_runtimes)} live runtime(s) for '{task_type}' are at capacity. "
            f"Recommended '{recommended}' — task may queue behind existing work."
        )
    else:
        recommended = None
        stale_names = ", ".join(stale_runtimes)
        reason = (
            f"All {len(stale_runtimes)} eligible runtime(s) for '{task_type}' are stale: "
            f"{stale_names}. No live runtime available."
        )

    return RouteResult(
        task_type=task_type,
        eligible_runtimes=eligible_runtimes,
        live_runtimes=live_runtimes,
        stale_runtimes=stale_runtimes,
        at_capacity_runtimes=at_capacity_runtimes,
        available_runtimes=available_runtimes,
        all_registered=all_registered,
        recommended=recommended,
        reason=reason,
    )
