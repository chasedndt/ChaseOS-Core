"""Inspect Layer C/D runtime memory substrates.

This module is read-only by design. It gives the CLI one bounded way to inspect
runtime-specific memory (Layer C), repair memory, and task-local memory (Layer D)
without letting memory override governance or current source truth.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from runtime.memory.scorecards.scorecard_updater import (
    list_scorecards,
    load_scorecard,
)


MEMORY_DIR = Path("runtime/memory")
ADAPTERS_DIR = MEMORY_DIR / "adapters"
REPAIR_DIR = MEMORY_DIR / "repair"
NAV_DIR = MEMORY_DIR / "nav"
SCORECARDS_DIR = MEMORY_DIR / "scorecards"
TASKS_DIR = Path("runtime/tasks")
TASKS_ACTIVE_DIR = TASKS_DIR / "active"
IDENTITY_LEDGER_FILENAME = "identity-ledger.json"

IDENTITY_LEDGER_REQUIRED_FIELDS = (
    "schema_version",
    "layer",
    "memory_family",
    "runtime_id",
    "status",
    "updated_at",
    "identity_summary",
    "behavioral_tendencies",
    "doctrine_adherence",
    "correction_history",
    "drift_signals",
    "governance_boundary",
)

IDENTITY_LEDGER_LIST_FIELDS = (
    "behavioral_tendencies",
    "correction_history",
    "drift_signals",
)

MEMORY_FAMILY_FIELDS = {
    "profile": "profile_present",
    "identity_ledger": "identity_ledger_present",
    "navigation": "nav_map_present",
    "scorecard": "scorecard_present",
    "repair_memory": "repair_memory_present",
}

MEMORY_STRUCTURE_REQUIRED_PATHS = [
    {
        "path": "runtime/memory/README.md",
        "kind": "file",
        "layer": "C/D",
        "surface": "operator documentation",
        "purpose": "human-readable Layer C/D memory structure guide",
    },
    {
        "path": "runtime/memory/adapters",
        "kind": "directory",
        "layer": "C",
        "surface": "Memory Manager / Agent Identity",
        "purpose": "runtime profiles and identity-ledger records",
    },
    {
        "path": "runtime/memory/adapters/_identity_ledger_schema.json",
        "kind": "file",
        "layer": "C",
        "surface": "Agent Identity Ledger",
        "purpose": "machine-readable identity-ledger schema foothold",
    },
    {
        "path": "runtime/memory/nav",
        "kind": "directory",
        "layer": "C",
        "surface": "Runtime Navigation",
        "purpose": "runtime navigation overlays",
    },
    {
        "path": "runtime/memory/nav/_schema.json",
        "kind": "file",
        "layer": "C",
        "surface": "Runtime Navigation",
        "purpose": "machine-readable navigation-map schema foothold",
    },
    {
        "path": "runtime/memory/repair",
        "kind": "directory",
        "layer": "C",
        "surface": "Memory Ledger / Support Loops",
        "purpose": "execution repair memory records",
    },
    {
        "path": "runtime/memory/repair/_schema.json",
        "kind": "file",
        "layer": "C",
        "surface": "Memory Ledger / Support Loops",
        "purpose": "machine-readable execution-repair schema foothold",
    },
    {
        "path": "runtime/memory/scorecards",
        "kind": "directory",
        "layer": "C",
        "surface": "Quality Review / Memory Manager",
        "purpose": "runtime execution scorecards",
    },
    {
        "path": "runtime/tasks",
        "kind": "directory",
        "layer": "D",
        "surface": "Tasks & Runs / Memory Ledger",
        "purpose": "task-local memory root",
    },
    {
        "path": "runtime/tasks/active",
        "kind": "directory",
        "layer": "D",
        "surface": "Tasks & Runs / Memory Ledger",
        "purpose": "active task-local context records",
    },
    {
        "path": "runtime/tasks/archive",
        "kind": "directory",
        "layer": "D",
        "surface": "Tasks & Runs / Memory Ledger",
        "purpose": "archived task-local context records",
    },
]


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def _json_or_default(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    try:
        if not path.exists():
            return dict(default)
        return _read_json(path)
    except Exception as exc:  # noqa: BLE001
        result = dict(default)
        result["load_error"] = str(exc)
        return result


def _runtime_dirs(vault_root: Path, rel_dir: Path) -> set[str]:
    path = vault_root / rel_dir
    if not path.exists():
        return set()
    return {
        item.name
        for item in path.iterdir()
        if item.is_dir() and not item.name.startswith("_")
    }


def _runtime_json_stems(vault_root: Path, rel_dir: Path) -> set[str]:
    path = vault_root / rel_dir
    if not path.exists():
        return set()
    return {
        item.stem
        for item in path.glob("*.json")
        if item.is_file() and not item.name.startswith("_")
    }


def list_runtime_memory(vault_root: Path) -> list[dict[str, Any]]:
    """Return runtime IDs with available Layer C memory families."""
    runtime_ids: set[str] = set()
    runtime_ids.update(_runtime_dirs(vault_root, ADAPTERS_DIR))
    runtime_ids.update(_runtime_dirs(vault_root, NAV_DIR))
    runtime_ids.update(_runtime_json_stems(vault_root, REPAIR_DIR))
    runtime_ids.update(list_scorecards(vault_root))

    result: list[dict[str, Any]] = []
    for runtime_id in sorted(runtime_ids):
        result.append(
            {
                "runtime_id": runtime_id,
                "profile_present": (vault_root / ADAPTERS_DIR / runtime_id / "profile.json").exists(),
                "identity_ledger_present": (vault_root / ADAPTERS_DIR / runtime_id / IDENTITY_LEDGER_FILENAME).exists(),
                "nav_map_present": (vault_root / NAV_DIR / runtime_id / "nav-map.json").exists(),
                "scorecard_present": (vault_root / SCORECARDS_DIR / f"{runtime_id}.json").exists(),
                "repair_memory_present": (vault_root / REPAIR_DIR / f"{runtime_id}.json").exists(),
            }
        )
    return result


def load_runtime_profile(runtime_id: str, vault_root: Path) -> dict[str, Any]:
    """Load a structured Layer C runtime profile."""
    default = {
        "schema_version": "1.0",
        "layer": "C",
        "runtime_id": runtime_id,
        "status": "missing",
        "behavioral_profile": {},
    }
    return _json_or_default(vault_root / ADAPTERS_DIR / runtime_id / "profile.json", default)


def load_identity_ledger(runtime_id: str, vault_root: Path) -> dict[str, Any]:
    """Load a structured Layer C Agent Identity Ledger."""
    default = {
        "schema_version": "1.0",
        "layer": "C",
        "memory_family": "agent_identity_ledger",
        "runtime_id": runtime_id,
        "status": "missing",
        "behavioral_tendencies": [],
        "doctrine_adherence": {},
        "correction_history": [],
        "drift_signals": [],
    }
    return _json_or_default(vault_root / ADAPTERS_DIR / runtime_id / IDENTITY_LEDGER_FILENAME, default)


def load_repair_memory(runtime_id: str, vault_root: Path) -> dict[str, Any]:
    """Load a structured execution repair memory file for one runtime."""
    default = {
        "schema_version": "1.0",
        "layer": "C",
        "memory_family": "execution_repair",
        "runtime_id": runtime_id,
        "status": "missing",
        "repair_patterns": [],
        "incident_candidates": [],
    }
    return _json_or_default(vault_root / REPAIR_DIR / f"{runtime_id}.json", default)


def load_nav_map(runtime_id: str, vault_root: Path) -> dict[str, Any]:
    """Load a runtime navigation overlay when present."""
    default = {
        "runtime_id": runtime_id,
        "status": "missing",
        "preferred_read_routes": [],
        "trusted_zones": [],
        "safe_write_paths": [],
        "risk_zones": [],
        "escalation_points": [],
    }
    return _json_or_default(vault_root / NAV_DIR / runtime_id / "nav-map.json", default)


def list_task_contexts(vault_root: Path) -> list[dict[str, Any]]:
    """List active Layer D task-local memory contexts."""
    active_dir = vault_root / TASKS_ACTIVE_DIR
    if not active_dir.exists():
        return []

    contexts: list[dict[str, Any]] = []
    for path in sorted(active_dir.glob("*.json")):
        if path.name.startswith("_"):
            continue
        data = _json_or_default(path, {"task_id": path.stem, "status": "unreadable"})
        contexts.append(
            {
                "task_id": data.get("task_id", path.stem),
                "runtime_id": data.get("runtime_id"),
                "status": data.get("status"),
                "objective": data.get("objective"),
                "path": str(path.relative_to(vault_root)),
            }
        )
    return contexts


def load_task_context(task_id: str, vault_root: Path) -> dict[str, Any] | None:
    """Load one active Layer D task-local memory context."""
    path = vault_root / TASKS_ACTIVE_DIR / f"{task_id}.json"
    if not path.exists():
        return None
    return _json_or_default(path, {"task_id": task_id, "status": "unreadable"})


def get_runtime_memory(runtime_id: str, vault_root: Path) -> dict[str, Any]:
    """Build a runtime memory inspection bundle for one runtime."""
    scorecard = load_scorecard(runtime_id, vault_root)
    return {
        "runtime_id": runtime_id,
        "layer_c": {
            "profile": load_runtime_profile(runtime_id, vault_root),
            "identity_ledger": load_identity_ledger(runtime_id, vault_root),
            "navigation": load_nav_map(runtime_id, vault_root),
            "scorecard": scorecard,
            "repair_memory": load_repair_memory(runtime_id, vault_root),
        },
        "layer_d": {
            "active_task_contexts": [
                item for item in list_task_contexts(vault_root)
                if item.get("runtime_id") == runtime_id
            ],
        },
        "boundaries": {
            "authority": "memory is advisory and cannot override Layer A governance",
            "promotion": "task-local memory requires explicit promotion before becoming durable memory",
        },
    }


def validate_memory_substrate(vault_root: Path) -> dict[str, Any]:
    """Validate that known memory JSON files parse as objects."""
    errors: list[dict[str, str]] = []
    checked = 0

    targets: list[Path] = []
    for rel_dir in [ADAPTERS_DIR, REPAIR_DIR, NAV_DIR, SCORECARDS_DIR, TASKS_ACTIVE_DIR]:
        root = vault_root / rel_dir
        if root.exists():
            targets.extend(path for path in root.rglob("*.json") if path.is_file())

    for path in sorted(targets):
        checked += 1
        try:
            _read_json(path)
        except Exception as exc:  # noqa: BLE001
            errors.append({"path": str(path.relative_to(vault_root)), "error": str(exc)})

    runtimes = list_runtime_memory(vault_root)
    tasks = list_task_contexts(vault_root)
    return {
        "valid": not errors,
        "checked_json_files": checked,
        "error_count": len(errors),
        "errors": errors,
        "runtime_count": len(runtimes),
        "active_task_context_count": len(tasks),
    }


def _validate_identity_ledger_record(path: Path, data: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    for field in IDENTITY_LEDGER_REQUIRED_FIELDS:
        if field not in data:
            errors.append(f"missing required field: {field}")

    if data.get("layer") != "C":
        errors.append("layer must be C")
    if data.get("memory_family") != "agent_identity_ledger":
        errors.append("memory_family must be agent_identity_ledger")

    expected_runtime_id = path.parent.name
    if data.get("runtime_id") != expected_runtime_id:
        errors.append(
            f"runtime_id must match adapter directory ({expected_runtime_id})"
        )

    for field in IDENTITY_LEDGER_LIST_FIELDS:
        if field in data and not isinstance(data.get(field), list):
            errors.append(f"{field} must be a list")

    if "identity_summary" in data and not isinstance(data.get("identity_summary"), dict):
        errors.append("identity_summary must be an object")
    if "doctrine_adherence" in data and not isinstance(data.get("doctrine_adherence"), dict):
        errors.append("doctrine_adherence must be an object")

    governance_boundary = data.get("governance_boundary")
    if "governance_boundary" in data and not (
        isinstance(governance_boundary, str) and governance_boundary.strip()
    ):
        errors.append("governance_boundary must be a non-empty string")

    return errors


def validate_identity_ledgers(vault_root: Path) -> dict[str, Any]:
    """Validate present Agent Identity Ledger records without mutating them."""
    ledger_root = vault_root / ADAPTERS_DIR
    errors: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []

    if not ledger_root.exists():
        return {
            "valid": True,
            "checked_identity_ledgers": 0,
            "valid_identity_ledgers": 0,
            "error_count": 0,
            "records": [],
            "errors": [],
        }

    for path in sorted(ledger_root.glob(f"*/{IDENTITY_LEDGER_FILENAME}")):
        rel_path = str(path.relative_to(vault_root))
        record: dict[str, Any] = {
            "runtime_id": path.parent.name,
            "path": rel_path,
            "valid": False,
        }
        try:
            data = _read_json(path)
            record_errors = _validate_identity_ledger_record(path, data)
        except Exception as exc:  # noqa: BLE001
            record_errors = [str(exc)]

        if record_errors:
            errors.append(
                {
                    "runtime_id": path.parent.name,
                    "path": rel_path,
                    "errors": record_errors,
                }
            )
            record["errors"] = record_errors
        else:
            record["valid"] = True
        records.append(record)

    return {
        "valid": not errors,
        "checked_identity_ledgers": len(records),
        "valid_identity_ledgers": sum(1 for record in records if record.get("valid")),
        "error_count": len(errors),
        "records": records,
        "errors": errors,
    }


def build_memory_status(vault_root: Path) -> dict[str, Any]:
    """Return a compact Layer C/D memory status view."""
    runtimes = list_runtime_memory(vault_root)
    tasks = list_task_contexts(vault_root)
    return {
        "layer_c": {
            "runtime_count": len(runtimes),
            "runtimes": runtimes,
            "profile_store": str(ADAPTERS_DIR),
            "identity_ledger_store": str(ADAPTERS_DIR),
            "repair_store": str(REPAIR_DIR),
            "scorecard_store": str(SCORECARDS_DIR),
            "navigation_store": str(NAV_DIR),
        },
        "layer_d": {
            "active_task_context_count": len(tasks),
            "active_task_contexts": tasks,
            "task_store": str(TASKS_DIR),
        },
    }


def _attention(code: str, severity: str, message: str, *, target: str | None = None) -> dict[str, str]:
    item = {
        "code": code,
        "severity": severity,
        "message": message,
    }
    if target:
        item["target"] = target
    return item


def _derive_memory_posture(attention_items: list[dict[str, str]]) -> str:
    if any(item.get("severity") == "error" for item in attention_items):
        return "blocked"
    if any(item.get("severity") == "warning" for item in attention_items):
        return "degraded"
    return "ready"


def _runtime_coverage(runtime: dict[str, Any]) -> dict[str, Any]:
    present = sorted(
        family
        for family, field in MEMORY_FAMILY_FIELDS.items()
        if runtime.get(field) is True
    )
    missing = sorted(
        family
        for family, field in MEMORY_FAMILY_FIELDS.items()
        if runtime.get(field) is not True
    )
    return {
        "runtime_id": runtime.get("runtime_id"),
        "present_families": present,
        "missing_families": missing,
        "complete": not missing,
    }


def build_memory_summary(vault_root: Path) -> dict[str, Any]:
    """Build a read-only consolidated Layer C/D memory summary."""
    status = build_memory_status(vault_root)
    validation = validate_memory_substrate(vault_root)
    identity_validation = validate_identity_ledgers(vault_root)
    runtimes = status["layer_c"]["runtimes"]
    tasks = status["layer_d"]["active_task_contexts"]

    runtime_coverage = [_runtime_coverage(runtime) for runtime in runtimes]
    family_counts = {
        family: sum(1 for runtime in runtimes if runtime.get(field) is True)
        for family, field in MEMORY_FAMILY_FIELDS.items()
    }

    attention_items: list[dict[str, str]] = []
    for error in validation.get("errors") or []:
        attention_items.append(
            _attention(
                "invalid_memory_json",
                "error",
                str(error.get("error") or "Memory JSON file failed validation"),
                target=str(error.get("path") or ""),
            )
        )

    for error in identity_validation.get("errors") or []:
        attention_items.append(
            _attention(
                "invalid_identity_ledger",
                "error",
                "; ".join(str(item) for item in error.get("errors") or [])
                or "Identity ledger failed schema/readiness validation",
                target=str(error.get("path") or error.get("runtime_id") or ""),
            )
        )

    if not runtimes:
        attention_items.append(
            _attention(
                "no_runtime_memory",
                "warning",
                "No Layer C runtime memory stores were found",
            )
        )

    for coverage in runtime_coverage:
        missing = coverage["missing_families"]
        if missing:
            attention_items.append(
                _attention(
                    "runtime_memory_incomplete",
                    "warning",
                    f"Runtime memory is missing families: {', '.join(missing)}",
                    target=str(coverage.get("runtime_id")),
                )
            )

    next_actions: list[str] = []
    if any(item.get("code") == "invalid_memory_json" for item in attention_items):
        next_actions.append("fix invalid memory JSON before relying on memory-inspector summaries")
    if any(item.get("code") == "invalid_identity_ledger" for item in attention_items):
        next_actions.append("repair identity ledger schema/readiness errors before treating Agent Identity as complete")
    if any(item.get("code") == "runtime_memory_incomplete" for item in attention_items):
        next_actions.append("fill missing runtime memory families only when backed by evidence; do not invent behavioral memory")
    if not tasks:
        next_actions.append("create Layer D task contexts only from active governed workflow/task execution, not as ambient memory")

    return {
        "action": "memory-summary",
        "memory_posture": _derive_memory_posture(attention_items),
        "read_only": True,
        "mutates_memory": False,
        "authority_expansion": False,
        "validation": validation,
        "identity_ledger_validation": identity_validation,
        "layer_c": status["layer_c"],
        "layer_d": status["layer_d"],
        "runtime_summary": {
            "runtime_count": len(runtimes),
            "family_counts": family_counts,
            "runtime_coverage": runtime_coverage,
        },
        "task_summary": {
            "active_task_context_count": len(tasks),
            "active_task_contexts": tasks,
        },
        "governance": {
            "memory_is_advisory": True,
            "memory_overrides_gate": False,
            "memory_overrides_source_truth": False,
            "task_memory_promotes_automatically": False,
            "identity_ledger_grants_authority": False,
            "repair_memory_applies_automatically": False,
        },
        "attention_items": attention_items,
        "next_actions": next_actions,
    }


def _artifact_rel_path(path: Path, vault_root: Path) -> str:
    try:
        return str(path.relative_to(vault_root))
    except ValueError:
        return str(path)


def _load_workspace_candidate(path: Path, vault_root: Path) -> dict[str, Any] | None:
    data = _json_or_default(path, {"load_error": "unreadable workspace"})
    outputs = data.get("outputs") if isinstance(data.get("outputs"), list) else []
    candidates = [
        item
        for item in outputs
        if isinstance(item, dict) and item.get("promotion_candidate") is True
    ]
    if not candidates:
        return None
    return {
        "workspace_id": data.get("workspace_id") or path.parent.name,
        "domain": data.get("domain"),
        "path": _artifact_rel_path(path, vault_root),
        "candidate_count": len(candidates),
        "candidates": candidates,
    }


def _load_durable_generated_artifact(path: Path, vault_root: Path) -> dict[str, Any] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if "knowledge_class:" not in text:
        return None

    knowledge_class = "unknown"
    endorsement_status = "unknown"
    for line in text.splitlines()[:40]:
        stripped = line.strip()
        if stripped.startswith("knowledge_class:"):
            knowledge_class = stripped.split(":", 1)[1].strip()
        if stripped.startswith("endorsement_status:"):
            endorsement_status = stripped.split(":", 1)[1].strip()

    if knowledge_class == "unknown":
        return None
    return {
        "path": _artifact_rel_path(path, vault_root),
        "knowledge_class": knowledge_class,
        "endorsement_status": endorsement_status,
    }


def build_generated_artifact_readiness(vault_root: Path) -> dict[str, Any]:
    """Inspect generated-artifact candidates without creating or promoting memory."""
    workspace_root = vault_root / "runtime/source_intelligence/workspaces"
    workspace_candidates: list[dict[str, Any]] = []
    if workspace_root.exists():
        for path in sorted(workspace_root.glob("*/workspace.json")):
            candidate = _load_workspace_candidate(path, vault_root)
            if candidate is not None:
                workspace_candidates.append(candidate)

    durable_root = vault_root / "02_KNOWLEDGE"
    durable_artifacts: list[dict[str, Any]] = []
    if durable_root.exists():
        for path in sorted(durable_root.rglob("*.md")):
            artifact = _load_durable_generated_artifact(path, vault_root)
            if artifact is not None and artifact.get("knowledge_class") == "generated-ideas":
                durable_artifacts.append(artifact)

    status = "no-generated-artifacts"
    if workspace_candidates and durable_artifacts:
        status = "durable-artifacts-present"
    elif workspace_candidates:
        status = "workspace-candidates-present"
    elif durable_artifacts:
        status = "durable-artifacts-present"

    return {
        "action": "memory-generated-artifacts",
        "source_backlog_id": "NB-010",
        "status": status,
        "read_only": True,
        "mutates_vault": False,
        "creates_generated_ideas_directories": False,
        "gate_required_for_promotion": True,
        "workspace_candidate_count": len(workspace_candidates),
        "durable_artifact_count": len(durable_artifacts),
        "workspace_candidates": workspace_candidates,
        "durable_artifacts": durable_artifacts,
        "blocked_actions": [
            "no Layer C file or directory creation without explicit Gate promotion",
            "no canonical promotion or endorsement status mutation from this status surface",
        ],
    }


def build_memory_file_structure(vault_root: Path) -> dict[str, Any]:
    """Return the read-only Agent Memory Architecture file-structure contract."""
    entries: list[dict[str, Any]] = []
    missing: list[str] = []

    for spec in MEMORY_STRUCTURE_REQUIRED_PATHS:
        path = vault_root / spec["path"]
        exists = path.is_dir() if spec["kind"] == "directory" else path.is_file()
        if not exists:
            missing.append(spec["path"])
        entries.append({**spec, "exists": exists})

    return {
        "action": "memory-file-structure",
        "source_backlog_id": "NB-006",
        "status": "ready" if not missing else "degraded",
        "read_only": True,
        "mutates_memory": False,
        "authority_expansion": False,
        "product_surfaces": ["Memory Manager", "Memory Ledger", "Context Import"],
        "required_paths": entries,
        "missing_paths": missing,
        "governance": {
            "layer_c_is_advisory": True,
            "layer_d_promotes_automatically": False,
            "memory_writes_require_governed_apply_flow": True,
            "canonical_promotion_allowed_from_this_surface": False,
            "runtime_dispatch_allowed_from_this_surface": False,
        },
        "blocked_lower_authority_lane": {
            "blocked_action": "automatic memory mutation, runtime dispatch, and canonical promotion",
            "minimum_proof_needed": "operator-approved apply packet plus exact target path/digest before any durable memory write",
            "owner_surface": "governed Memory Manager / Context Import apply flow",
        },
        "ui_contract": {
            "title": "Agent Memory Architecture File Structure",
            "primary_command": "chaseos memory structure --json",
            "summary_command": "chaseos memory structure",
            "operator_can_inspect": [
                "Layer C runtime memory homes",
                "Layer D task-local memory homes",
                "missing file-structure paths",
                "blocked mutation/promotion boundary",
            ],
        },
    }
