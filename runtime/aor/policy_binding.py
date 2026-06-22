"""
policy_binding.py — ChaseOS Phase 9 runtime policy-binding substrate.

Creates immutable-ish machine-readable policy binding records under:
    runtime/aor/runtime_registry/<runtime_id>/policy_binding.yaml

This is the bridge between runtime registration and execution-capable posture.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional

from runtime.aor.runtime_registry import load_runtime_entry, _entry_path, _load_yaml_file, _dump_yaml_file
from runtime.aor.task_router import classify
from runtime.gate_interface import load_adapter_manifest


def _now_utc_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _binding_path(runtime_id: str, vault_root: Optional[Path] = None) -> Path:
    return _entry_path(runtime_id, vault_root).parent / "policy_binding.yaml"


def load_policy_binding(runtime_id: str, vault_root: Optional[Path] = None) -> dict[str, Any] | None:
    path = _binding_path(runtime_id, vault_root)
    if not path.exists():
        return None
    return _load_yaml_file(path)


def bind_runtime_policy(runtime_id: str, vault_root: Optional[Path] = None) -> dict[str, Any]:
    entry = load_runtime_entry(runtime_id, vault_root=vault_root)
    if entry is None:
        raise ValueError(f"Runtime '{runtime_id}' is not registered")

    adapter_id = str(entry.get("provider", "")).strip()
    manifest = load_adapter_manifest(adapter_id)
    if not manifest:
        raise ValueError(f"Adapter manifest not found for provider/adapter '{adapter_id}'")

    allowed_task_types = list(manifest.get("allowed_task_types") or [])
    task_type_contracts: list[dict[str, Any]] = []
    for task_type in allowed_task_types:
        definition = classify(task_type, vault_root=vault_root)
        if definition.get("id") == "unclassified":
            continue
        task_type_contracts.append(
            {
                "task_type": definition.get("id"),
                "required_reads": definition.get("required_reads") or [],
                "optional_reads": definition.get("optional_reads") or [],
                "permission_ceiling": definition.get("permission_ceiling"),
                "runtime_class": definition.get("runtime_class"),
                "writeback_expectations": definition.get("writeback_expectations"),
                "escalation_trigger": definition.get("escalation_trigger") or [],
            }
        )

    write_targets = manifest.get("allowed_write_targets") or {}
    enabled_write_targets = sorted([key for key, enabled in write_targets.items() if enabled is True])

    required_read_sets = manifest.get("required_read_sets") or {}
    flattened_required_read_rules = sorted(
        {
            item
            for read_list in required_read_sets.values()
            if isinstance(read_list, list)
            for item in read_list
        }
    )

    binding = {
        "runtime_id": runtime_id,
        "adapter_id": manifest.get("adapter_id", adapter_id),
        "trust_ceiling": manifest.get("trust_ceiling", entry.get("trust_ceiling")),
        "allowed_task_types": allowed_task_types,
        "required_read_rules": flattened_required_read_rules,
        "writeback_targets": enabled_write_targets,
        "promotion_rules": manifest.get("promotion_behavior") or {},
        "audit_log_target": manifest.get("audit_log_target"),
        "escalation_boundaries": {
            "protected_file_behavior": manifest.get("protected_file_behavior"),
            "explicitly_denied_write_targets": manifest.get("explicitly_denied_write_targets") or [],
            "coordination_policy": manifest.get("coordination_policy") or {},
            "approval_mode": manifest.get("approval_mode"),
        },
        "task_type_contracts": task_type_contracts,
        "policy_binding_complete": True,
        "generated_at": _now_utc_iso(),
    }

    path = _binding_path(runtime_id, vault_root)
    _dump_yaml_file(path, binding)

    entry_path = _entry_path(runtime_id, vault_root)
    entry["policy_binding_record"] = str(path)
    entry["last_evaluated"] = _now_utc_iso()
    _dump_yaml_file(entry_path, entry)

    return {
        "runtime_id": runtime_id,
        "binding_path": str(path),
        "allowed_task_types": allowed_task_types,
        "policy_binding_complete": True,
    }
