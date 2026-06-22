"""
runtime/commerce/flags.py — feature-flag resolver (ADR-0004).

Rollout/cohort toggles, distinct from entitlements. Default-DENY: an unknown or
disabled flag resolves False. Mirrors the runtime/schedules enabled/shadow_mode
precedent — in-place JSON state + a JSONL audit trail on every change. Pure stdlib.

The full gate is: is_enabled(flag) AND entitlements.check(plan, feature).allowed.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_FLAGS_PATH = Path(__file__).resolve().parent / "flags.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_flags(path: str | Path | None = None) -> dict[str, Any]:
    p = Path(path) if path else _FLAGS_PATH
    if not p.is_file():
        return {"flags_version": "0", "flags": {}}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"flags_version": "0", "flags": {}}  # fail-closed: empty → all disabled


def is_enabled(flag_id: str, *, cohort: Optional[str] = None, path: str | Path | None = None) -> bool:
    """True only if the flag exists, is enabled, and the cohort is permitted."""
    flags = load_flags(path).get("flags", {})
    entry = flags.get(flag_id)
    if not isinstance(entry, dict) or not entry.get("enabled"):
        return False
    cohorts = entry.get("cohorts", "all")
    if cohorts == "all":
        return True
    if not isinstance(cohorts, list):
        return False
    if cohort is None:
        return False
    return cohort in cohorts


def set_flag(
    flag_id: str,
    enabled: bool,
    *,
    cohorts: Any = None,
    actor: str = "operator",
    reason: str = "",
    path: str | Path | None = None,
    audit_path: str | Path | None = None,
) -> dict[str, Any]:
    """Toggle a flag in place and append a JSONL audit record. Returns the new entry."""
    p = Path(path) if path else _FLAGS_PATH
    data = load_flags(p)
    flags = data.setdefault("flags", {})
    before = dict(flags.get(flag_id, {}))
    entry = flags.setdefault(flag_id, {"cohorts": "all", "description": ""})
    entry["enabled"] = bool(enabled)
    if cohorts is not None:
        entry["cohorts"] = cohorts
    p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    record = {
        "ts": _now_iso(),
        "flag_id": flag_id,
        "actor": actor,
        "reason": reason,
        "before": before,
        "after": dict(entry),
    }
    ap = Path(audit_path) if audit_path else (p.parent / "flag-state-audit.jsonl")
    try:
        with ap.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError:
        pass  # audit is best-effort; never block the toggle
    return entry
