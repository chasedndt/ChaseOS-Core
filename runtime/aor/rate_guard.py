"""
rate_guard.py — ChaseOS AOR per-workflow daily execution rate guard (H-3)

Tracks per-workflow execution counts per UTC day in a lightweight JSON file.
Counts reset automatically at UTC midnight.

State file: <vault>/.chaseos/rate_guard.json
  {"date_utc": "YYYY-MM-DD", "counts": {"workflow_id": int, ...}}

Fail-open: any I/O error allows execution to proceed. Rate guard errors must
never block scheduled work — they degrade gracefully and log nothing to avoid
noise on every failing filesystem edge case.

Enforcement point: engine.py pre-stage rate_check, wired after context_boot.
Only fires when the workflow's schedule intent declares max_cycles_per_day.

Public API:
    is_rate_limited(workflow_id, max_cycles_per_day, vault_root) -> bool
    record_execution(workflow_id, vault_root) -> int
    get_execution_count(workflow_id, vault_root) -> int
    get_rate_guard_state(vault_root) -> dict
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ── Internal helpers ──────────────────────────────────────────────────────────

def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _state_path(vault_root: Path) -> Path:
    return vault_root / ".chaseos" / "rate_guard.json"


def _load_state(vault_root: Path) -> dict:
    today = _today_utc()
    try:
        path = _state_path(vault_root)
        if not path.exists():
            return {"date_utc": today, "counts": {}}
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {"date_utc": today, "counts": {}}
        if raw.get("date_utc") != today:
            # UTC day boundary — reset counts
            return {"date_utc": today, "counts": {}}
        counts = raw.get("counts", {})
        if not isinstance(counts, dict):
            counts = {}
        return {"date_utc": today, "counts": {k: v for k, v in counts.items() if isinstance(v, int)}}
    except Exception:  # noqa: BLE001
        return {"date_utc": today, "counts": {}}


def _save_state(state: dict, vault_root: Path) -> None:
    try:
        path = _state_path(vault_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


# ── Public API ────────────────────────────────────────────────────────────────

def is_rate_limited(
    workflow_id: str,
    max_cycles_per_day: Optional[int],
    vault_root: Path,
) -> bool:
    """
    Return True if workflow_id has reached its daily cycle limit. Fail-open.

    Parameters
    ----------
    workflow_id : str
        The workflow being checked.
    max_cycles_per_day : int or None
        The daily cycle ceiling. None or 0 means no limit — returns False.
    vault_root : Path
        Vault root for state file resolution.
    """
    if max_cycles_per_day is None or max_cycles_per_day <= 0:
        return False
    try:
        state = _load_state(vault_root)
        count = int(state["counts"].get(workflow_id, 0))
        return count >= max_cycles_per_day
    except Exception:  # noqa: BLE001
        return False  # fail-open


def record_execution(workflow_id: str, vault_root: Path) -> int:
    """
    Increment the cycle counter for workflow_id for today (UTC).

    Returns the new count. Fail-open: returns 0 on any I/O error.
    """
    try:
        state = _load_state(vault_root)
        current = int(state["counts"].get(workflow_id, 0))
        new_count = current + 1
        state["counts"][workflow_id] = new_count
        _save_state(state, vault_root)
        return new_count
    except Exception:  # noqa: BLE001
        return 0


def get_execution_count(workflow_id: str, vault_root: Path) -> int:
    """Return the current cycle count for workflow_id today (UTC). Fail-open."""
    try:
        state = _load_state(vault_root)
        return int(state["counts"].get(workflow_id, 0))
    except Exception:  # noqa: BLE001
        return 0


def get_rate_guard_state(vault_root: Path) -> dict:
    """
    Return the full rate guard state dict.

    Returns {"date_utc": str, "counts": {workflow_id: int, ...}}.
    Fail-open: returns a clean empty-counts dict on any error.
    """
    try:
        return _load_state(vault_root)
    except Exception:  # noqa: BLE001
        return {"date_utc": _today_utc(), "counts": {}}
