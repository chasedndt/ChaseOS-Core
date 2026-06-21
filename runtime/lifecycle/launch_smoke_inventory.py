"""Read-only launch-smoke inventory surface.

The inventory intentionally does not run host startup probes, schedulers, or
configuration writers. It only reports the command/test checklist an operator or
CI lane can run to verify launch-smoke readiness.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]

_LIFECYCLE_RUNTIME_IDS = ("hermes", "openclaw")

_STATUS_COMMANDS: tuple[dict[str, str], ...] = (
    {
        "id": "startup-surfaces",
        "label": "Startup surface status for all runtimes",
        "command": "chaseos runtime startup-surfaces --runtime all --json",
        "mutates_host_startup_state": "false",
    },
    {
        "id": "hermes-gateway-config",
        "label": "Hermes private gateway config readiness",
        "command": "chaseos runtime hermes-gateway-config --action status --json",
        "mutates_host_startup_state": "false",
    },
    {
        "id": "coordination-watch-bootstrap",
        "label": "Hermes coordination-watch bootstrap activation report",
        "command": "chaseos runtime coordination-watch-bootstrap --runtime hermes --action activation-report --json",
        "mutates_host_startup_state": "false",
    },
)

_PYTEST_TARGET_PATHS = (
    "runtime/lifecycle/test_startup_surfaces.py",
    "runtime/lifecycle/test_hermes_gateway_config.py",
    "runtime/test_runtime_startup_surfaces_cli.py",
    "runtime/test_chaseos_startup_surfaces_cli.py",
)


def _normalize_vault_root(vault_root: str | Path | None = None) -> Path:
    return Path(vault_root).resolve() if vault_root is not None else ROOT


def _file_entry(vault_root: Path, relative_path: str, **extra: Any) -> dict[str, Any]:
    path = vault_root / relative_path
    return {
        "path": relative_path,
        "absolute_path": str(path),
        "exists": path.exists(),
        **extra,
    }


def _status_command_entries() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for item in _STATUS_COMMANDS:
        entries.append(
            {
                "id": item["id"],
                "label": item["label"],
                "command": item["command"],
                "mutates_host_startup_state": item["mutates_host_startup_state"] == "true",
            }
        )
    return entries


def build_launch_smoke_inventory(vault_root: str | Path | None = None) -> dict[str, Any]:
    """Build a read-only manifest of launch-smoke checks and test targets."""

    root = _normalize_vault_root(vault_root)
    lifecycle_files = [
        _file_entry(
            root,
            f"runtime/lifecycle/{runtime_id}.lifecycle.yaml",
            runtime_id=runtime_id,
        )
        for runtime_id in _LIFECYCLE_RUNTIME_IDS
    ]
    pytest_targets = [
        _file_entry(
            root,
            relative_path,
            command=f"PYTHONPATH=. uvx --with pyyaml --with pytest pytest {relative_path} -q",
        )
        for relative_path in _PYTEST_TARGET_PATHS
    ]
    missing_lifecycle = [item["runtime_id"] for item in lifecycle_files if not item["exists"]]
    missing_pytest_targets = [item["path"] for item in pytest_targets if not item["exists"]]

    return {
        "action": "launch-smoke-inventory",
        "read_only": True,
        "mutation_enabled": False,
        "mutates_host_startup_state": False,
        "vault_root": str(root),
        "lifecycle_files": lifecycle_files,
        "status_commands": _status_command_entries(),
        "pytest_targets": pytest_targets,
        "write_targets": [],
        "notes": [
            "Inventory surface only: it does not invoke host schedulers, toggle startup state, edit gateway config, or write files.",
            "Run listed status commands and pytest targets explicitly when launch-smoke proof is needed.",
        ],
        "missing": {
            "lifecycle_files": missing_lifecycle,
            "pytest_targets": missing_pytest_targets,
        },
    }


def format_launch_smoke_inventory(result: dict[str, Any]) -> str:
    """Return a compact human-readable inventory report."""

    lines = [
        "ChaseOS Runtime Launch-Smoke Inventory",
        f"  read_only: {result.get('read_only')}",
        f"  mutation_enabled: {result.get('mutation_enabled')}",
        f"  mutates_host_startup_state: {result.get('mutates_host_startup_state')}",
        f"  vault_root: {result.get('vault_root')}",
        "",
        "Lifecycle files:",
    ]
    for item in result.get("lifecycle_files", []):
        lines.append(
            f"  - {item.get('runtime_id')}: {item.get('path')} exists={item.get('exists')}"
        )

    lines.extend(["", "Status commands:"])
    for item in result.get("status_commands", []):
        lines.append(f"  - {item.get('id')}: {item.get('command')}")

    lines.extend(["", "Pytest targets:"])
    for item in result.get("pytest_targets", []):
        lines.append(f"  - {item.get('path')} exists={item.get('exists')}")

    missing = result.get("missing") or {}
    if missing.get("lifecycle_files") or missing.get("pytest_targets"):
        lines.extend(["", f"Missing: {missing}"])

    notes = result.get("notes") or []
    if notes:
        lines.extend(["", "Notes:"])
        for note in notes:
            lines.append(f"  - {note}")
    return "\n".join(lines)
