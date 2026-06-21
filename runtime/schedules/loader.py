"""
loader.py — ChaseOS Native Schedule Intent Loader (Phase 9)

Loads, validates, and mutates schedule intent files in runtime/schedules/.

Schedule intent files are YAML. One file per schedule. File name = schedule_id.yaml.
index.yaml is the auto-maintained summary; it is not the source of truth.

Ownership boundary:
  ChaseOS owns:  schedule intent, enabled/disabled state, workflow linkage,
                 delivery policy, approval policy, provenance, audit requirements
  Runtime owns:  execution mechanics, transport, cron daemon, approval UI

Validation rules (fail closed on any violation):
  - schedule_id must match filename stem
  - required fields must all be present
  - workflow schedules: workflow_id must exist in workflow registry
  - workflow schedules: workflow must be status=active if schedule is enabled
  - workflow schedules: workflow task_type must be in SCHEDULABLE_TASK_TYPES
  - command schedules: command_id must be in VALID_SCHEDULE_COMMANDS
  - command schedules: command, when present, must match the registered command
  - runtime_adapter_target must be in VALID_RUNTIME_ADAPTERS
  - delivery.primary_target must be in VALID_DELIVERY_TARGETS
  - approval_policy must be in VALID_APPROVAL_POLICIES
  - failure_behavior must be in VALID_FAILURE_BEHAVIORS
  - cadence.type must be in VALID_CADENCE_TYPES
  - if cadence.type == "cron", cron_expression and timezone are required

Public API:
    load_schedule(schedule_id, vault_root=None) -> ScheduleIntent | None
    list_schedules(vault_root=None) -> list[ScheduleIntent]
    validate_all_schedules(vault_root=None) -> list[tuple[str, str]]
    enable_schedule(schedule_id, vault_root=None) -> bool
    disable_schedule(schedule_id, vault_root=None) -> bool
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Any

try:
    import yaml  # type: ignore
except Exception:
    yaml = None


# ── Constants ─────────────────────────────────────────────────────────────────

VALID_RUNTIME_ADAPTERS: set[str] = {"openclaw", "hermes", "claude", "archon", "n8n", "local", "manual"}
VALID_DELIVERY_TARGETS: set[str] = {"vault-local", "discord", "email", "whop", "slack"}
VALID_APPROVAL_POLICIES: set[str] = {"none", "pre-execution", "pre-delivery"}
VALID_FAILURE_BEHAVIORS: set[str] = {"escalate", "retry-once-then-escalate", "silent-fail-log"}
VALID_CADENCE_TYPES: set[str] = {"cron", "event", "manual", "webhook"}
VALID_SCHEDULE_KINDS: set[str] = {"workflow", "command"}

# Command schedules are intentionally narrow. They let adapter cron invoke
# bounded ChaseOS CLI loops without opening schedule intent to arbitrary shell.
VALID_SCHEDULE_COMMANDS: dict[str, str] = {
    "events.watch": "chaseos events watch --once --execute",
}

# Task types allowed to be scheduled. Must match task_type_table.yaml IDs.
SCHEDULABLE_TASK_TYPES: set[str] = {
    "operator-briefing",
    "graph-hygiene",
    "os-graph-maintenance",
    "idea-graduation",
    "scheduled-briefing",
    "source-pack-builder",
    "evidence-gated-market-analysis",
    "coordination",
    "behavior-audit",
}

# Permission ceilings that are NEVER schedulable regardless of task type.
NEVER_SCHEDULABLE_CEILINGS: set[str] = {
    "protected_file_writes",
    "canonical_promotion",
}

BASE_SCHEDULE_REQUIRED_FIELDS: list[str] = [
    "schedule_id",
    "owner",
    "cadence",
    "trigger_source",
    "runtime_adapter_target",
    "delivery",
    "approval_policy",
    "enabled",
    "shadow_mode",
    "failure_behavior",
    "audit_requirements",
    "provenance",
]

WORKFLOW_SCHEDULE_REQUIRED_FIELDS: list[str] = [
    *BASE_SCHEDULE_REQUIRED_FIELDS,
    "workflow_id",
    "allowed_workflow_task_types",
]

COMMAND_SCHEDULE_REQUIRED_FIELDS: list[str] = [
    *BASE_SCHEDULE_REQUIRED_FIELDS,
    "schedule_kind",
    "command_id",
    "allowed_command_ids",
]

SCHEDULE_REQUIRED_FIELDS: list[str] = WORKFLOW_SCHEDULE_REQUIRED_FIELDS

DELIVERY_REQUIRED_FIELDS: list[str] = [
    "primary_target",
    "vault_writeback_targets",
    "external_delivery_declared",
    "vault_local_only",
]

PROVENANCE_REQUIRED_FIELDS: list[str] = [
    "created_by",
    "created_at",
    "rationale",
]


def _coerce_scalar(value: str) -> Any:
    value = value.strip()
    if value == "[]":
        return []
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1]
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    if value.lower() == "null":
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _parse_yaml_mapping(text: str) -> dict[str, Any]:
    def _indent(line: str) -> int:
        return len(line) - len(line.lstrip(" "))

    def _next_content_index(lines: list[str], start: int) -> int:
        j = start
        while j < len(lines):
            s = lines[j].strip()
            if s and not s.startswith("#") and s != "---":
                return j
            j += 1
        return j

    def _parse_block_scalar(lines: list[str], start: int, indent: int, folded: bool) -> tuple[str, int]:
        parts: list[str] = []
        i = start
        while i < len(lines):
            raw = lines[i].rstrip("\n")
            stripped = raw.strip()
            current_indent = _indent(raw)
            if not stripped:
                parts.append("")
                i += 1
                continue
            if stripped.startswith("#"):
                i += 1
                continue
            if current_indent < indent:
                break
            parts.append(raw[indent:])
            i += 1
        if folded:
            text_value = " ".join(part for part in parts if part != "").strip()
        else:
            text_value = "\n".join(parts).strip()
        return text_value, i

    def _parse_block(lines: list[str], start: int, indent: int) -> tuple[Any, int]:
        i = _next_content_index(lines, start)
        if i >= len(lines):
            return {}, i
        raw = lines[i].rstrip()
        current_indent = _indent(raw)
        stripped = raw.strip()
        if current_indent < indent:
            return {}, i
        if stripped.startswith("- ") and current_indent == indent:
            items: list[Any] = []
            while i < len(lines):
                i = _next_content_index(lines, i)
                if i >= len(lines):
                    break
                raw = lines[i].rstrip()
                current_indent = _indent(raw)
                stripped = raw.strip()
                if current_indent != indent or not stripped.startswith("- "):
                    break
                item_value = stripped[2:].strip()
                if not item_value:
                    child, next_i = _parse_block(lines, i + 1, indent + 2)
                    items.append(child)
                    i = next_i
                    continue
                if ":" not in item_value:
                    items.append(_coerce_scalar(item_value))
                    i += 1
                    continue
                item_key, item_rest = item_value.split(":", 1)
                item_key = item_key.strip()
                item_rest = item_rest.strip()
                entry: dict[str, Any] = {}
                if item_rest in {">", "|"}:
                    scalar, next_i = _parse_block_scalar(lines, i + 1, indent + 2, folded=item_rest == ">")
                    entry[item_key] = scalar
                    i = next_i
                elif item_rest:
                    entry[item_key] = _coerce_scalar(item_rest)
                    i += 1
                else:
                    child, next_i = _parse_block(lines, i + 1, indent + 2)
                    entry[item_key] = child
                    i = next_i
                while i < len(lines):
                    j = _next_content_index(lines, i)
                    if j >= len(lines):
                        i = j
                        break
                    raw2 = lines[j].rstrip()
                    indent2 = _indent(raw2)
                    stripped2 = raw2.strip()
                    if indent2 < indent + 2:
                        break
                    if indent2 != indent + 2 or ":" not in stripped2:
                        raise ValueError(f"Unsupported YAML syntax on line {j + 1}: {raw2}")
                    subkey, subrest = stripped2.split(":", 1)
                    subkey = subkey.strip()
                    subrest = subrest.strip()
                    if subrest in {">", "|"}:
                        scalar, next_i = _parse_block_scalar(lines, j + 1, indent + 4, folded=subrest == ">")
                        entry[subkey] = scalar
                        i = next_i
                    elif subrest:
                        entry[subkey] = _coerce_scalar(subrest)
                        i = j + 1
                    else:
                        child, next_i = _parse_block(lines, j + 1, indent + 4)
                        entry[subkey] = child
                        i = next_i
                items.append(entry)
            return items, i

        mapping: dict[str, Any] = {}
        while i < len(lines):
            i = _next_content_index(lines, i)
            if i >= len(lines):
                break
            raw = lines[i].rstrip()
            current_indent = _indent(raw)
            stripped = raw.strip()
            if current_indent < indent:
                break
            if current_indent != indent or ":" not in stripped:
                raise ValueError(f"Unsupported YAML syntax on line {i + 1}: {raw}")
            key, rest = stripped.split(":", 1)
            key = key.strip()
            rest = rest.strip()
            if rest in {">", "|"}:
                scalar, next_i = _parse_block_scalar(lines, i + 1, indent + 2, folded=rest == ">")
                mapping[key] = scalar
                i = next_i
            elif rest:
                mapping[key] = _coerce_scalar(rest)
                i += 1
            else:
                child, next_i = _parse_block(lines, i + 1, indent + 2)
                mapping[key] = child
                i = next_i
        return mapping, i

    lines = text.splitlines()
    result, i = _parse_block(lines, 0, 0)
    if not isinstance(result, dict) or not result or i == 0:
        raise ValueError("Schedule manifest is empty or unsupported")
    return result


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8-sig")
    try:
        if yaml is not None:
            data = yaml.safe_load(text)
        else:
            data = _parse_yaml_mapping(text)
    except Exception as exc:
        raise ValueError(f"Failed to parse YAML at {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"YAML at {path} did not parse as a mapping")
    return data


def _dump_simple_yaml(data: Any, indent: int = 0) -> str:
    prefix = " " * indent
    if isinstance(data, dict):
        lines: list[str] = []
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                lines.append(f"{prefix}{key}:")
                lines.append(_dump_simple_yaml(value, indent + 2))
            else:
                scalar = "true" if value is True else "false" if value is False else "null" if value is None else f'"{value}"' if isinstance(value, str) else str(value)
                lines.append(f"{prefix}{key}: {scalar}")
        return "\n".join(lines)
    if isinstance(data, list):
        lines = []
        for item in data:
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}-")
                lines.append(_dump_simple_yaml(item, indent + 2))
            else:
                scalar = "true" if item is True else "false" if item is False else "null" if item is None else f'"{item}"' if isinstance(item, str) else str(item)
                lines.append(f"{prefix}- {scalar}")
        return "\n".join(lines)
    return f"{prefix}{data}"


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class ScheduleCadence:
    type: str
    cron_expression: Optional[str] = None
    timezone: Optional[str] = None
    event_type: Optional[str] = None
    event_source: Optional[str] = None


@dataclass
class ScheduleDelivery:
    primary_target: str
    vault_writeback_targets: list[str]
    external_delivery_declared: bool
    vault_local_only: bool


@dataclass
class ScheduleProvenance:
    created_by: str
    created_at: str
    rationale: str


@dataclass
class ScheduleIntent:
    schedule_id: str
    workflow_id: Optional[str]
    owner: str
    cadence: ScheduleCadence
    trigger_source: str
    runtime_adapter_target: str
    delivery: ScheduleDelivery
    approval_policy: str
    enabled: bool
    shadow_mode: bool
    failure_behavior: str
    audit_requirements: list[str]
    allowed_workflow_task_types: list[str]
    provenance: ScheduleProvenance
    schedule_kind: str = "workflow"
    command_id: Optional[str] = None
    command: Optional[str] = None
    allowed_command_ids: list[str] = field(default_factory=list)
    notes: Optional[str] = None
    runtime_adapter_fallback: Optional[str] = None
    max_cycles_per_day: Optional[int] = None
    _source_path: Optional[Path] = field(default=None, repr=False, compare=False)


# ── Path helpers ──────────────────────────────────────────────────────────────

def _detect_vault_root() -> Path:
    here = Path(__file__).resolve()
    vault_root = here.parents[2]  # runtime/schedules/loader.py → vault root
    if not (vault_root / "CLAUDE.md").exists():
        raise RuntimeError(
            f"Could not detect vault root. Expected CLAUDE.md at: {vault_root}\n"
            "Use vault_root parameter to specify the vault path explicitly."
        )
    return vault_root


def _get_schedules_dir(vault_root: Path) -> Path:
    return vault_root / "runtime" / "schedules"


def _get_workflow_registry_dir(vault_root: Path) -> Path:
    return vault_root / "runtime" / "workflows" / "registry"


def _get_state_log_path(vault_root: Path) -> Path:
    return vault_root / "07_LOGS" / "Schedule-State" / "schedule_state_log.jsonl"


# ── Validation ────────────────────────────────────────────────────────────────

def _validate_schedule(
    data: dict,
    path: Path,
    vault_root: Path,
    *,
    check_registry: bool = True,
) -> None:
    """
    Validate a schedule intent dict. Raises ValueError with a descriptive message
    if invalid. Fails closed on any violation.

    Parameters
    ----------
    data : dict
        Parsed YAML data from the schedule intent file.
    path : Path
        File path (used for error messages).
    vault_root : Path
        Vault root for workflow registry cross-reference.
    check_registry : bool
        If True, cross-reference the workflow registry. Set False in tests
        that provide a partial environment.
    """
    schedule_kind = data.get("schedule_kind", "workflow")
    if schedule_kind not in VALID_SCHEDULE_KINDS:
        raise ValueError(
            f"Schedule at {path}: schedule_kind {schedule_kind!r} must be one of: "
            f"{sorted(VALID_SCHEDULE_KINDS)}"
        )

    required_fields = (
        COMMAND_SCHEDULE_REQUIRED_FIELDS
        if schedule_kind == "command"
        else WORKFLOW_SCHEDULE_REQUIRED_FIELDS
    )

    # Required fields
    missing = [f for f in required_fields if f not in data]
    if missing:
        raise ValueError(
            f"Schedule at {path} is missing required fields: {missing}"
        )

    # schedule_id must match filename stem
    expected_id = path.stem
    if data.get("schedule_id") != expected_id:
        raise ValueError(
            f"Schedule at {path}: schedule_id ({data.get('schedule_id')!r}) "
            f"must match filename stem ({expected_id!r})"
        )

    # enabled must be boolean
    if not isinstance(data.get("enabled"), bool):
        raise ValueError(
            f"Schedule at {path}: 'enabled' must be a boolean (true/false), "
            f"got: {data.get('enabled')!r}"
        )

    # shadow_mode must be boolean
    if not isinstance(data.get("shadow_mode"), bool):
        raise ValueError(
            f"Schedule at {path}: 'shadow_mode' must be a boolean (true/false), "
            f"got: {data.get('shadow_mode')!r}"
        )

    # runtime_adapter_target — fail closed on unknown adapter
    adapter = data.get("runtime_adapter_target", "")
    if adapter not in VALID_RUNTIME_ADAPTERS:
        raise ValueError(
            f"Schedule at {path}: runtime_adapter_target {adapter!r} is not a "
            f"registered adapter. Must be one of: {sorted(VALID_RUNTIME_ADAPTERS)}"
        )

    # runtime_adapter_fallback — optional; if present must be valid and differ from primary
    fallback = data.get("runtime_adapter_fallback")
    if fallback is not None:
        if fallback not in VALID_RUNTIME_ADAPTERS:
            raise ValueError(
                f"Schedule at {path}: runtime_adapter_fallback {fallback!r} is not a "
                f"registered adapter. Must be one of: {sorted(VALID_RUNTIME_ADAPTERS)}"
            )
        if fallback == adapter:
            raise ValueError(
                f"Schedule at {path}: runtime_adapter_fallback {fallback!r} must differ "
                f"from runtime_adapter_target {adapter!r}"
            )

    # approval_policy
    approval = data.get("approval_policy", "")
    if approval not in VALID_APPROVAL_POLICIES:
        raise ValueError(
            f"Schedule at {path}: approval_policy {approval!r} must be one of: "
            f"{sorted(VALID_APPROVAL_POLICIES)}"
        )

    # failure_behavior
    failure = data.get("failure_behavior", "")
    if failure not in VALID_FAILURE_BEHAVIORS:
        raise ValueError(
            f"Schedule at {path}: failure_behavior {failure!r} must be one of: "
            f"{sorted(VALID_FAILURE_BEHAVIORS)}"
        )

    # cadence
    cadence = data.get("cadence")
    if not isinstance(cadence, dict):
        raise ValueError(f"Schedule at {path}: 'cadence' must be a mapping")
    cadence_type = cadence.get("type", "")
    if cadence_type not in VALID_CADENCE_TYPES:
        raise ValueError(
            f"Schedule at {path}: cadence.type {cadence_type!r} must be one of: "
            f"{sorted(VALID_CADENCE_TYPES)}"
        )
    if cadence_type == "cron":
        if not cadence.get("cron_expression"):
            raise ValueError(
                f"Schedule at {path}: cadence.cron_expression is required when cadence.type=cron"
            )
        if not cadence.get("timezone"):
            raise ValueError(
                f"Schedule at {path}: cadence.timezone is required when cadence.type=cron"
            )

    # delivery — fail closed on unknown target
    delivery = data.get("delivery")
    if not isinstance(delivery, dict):
        raise ValueError(f"Schedule at {path}: 'delivery' must be a mapping")
    missing_delivery = [f for f in DELIVERY_REQUIRED_FIELDS if f not in delivery]
    if missing_delivery:
        raise ValueError(
            f"Schedule at {path}: delivery is missing required fields: {missing_delivery}"
        )
    primary_target = delivery.get("primary_target", "")
    if primary_target not in VALID_DELIVERY_TARGETS:
        raise ValueError(
            f"Schedule at {path}: delivery.primary_target {primary_target!r} is not a "
            f"valid delivery target. Must be one of: {sorted(VALID_DELIVERY_TARGETS)}"
        )

    # provenance
    provenance = data.get("provenance")
    if not isinstance(provenance, dict):
        raise ValueError(f"Schedule at {path}: 'provenance' must be a mapping")
    missing_prov = [f for f in PROVENANCE_REQUIRED_FIELDS if f not in provenance]
    if missing_prov:
        raise ValueError(
            f"Schedule at {path}: provenance is missing required fields: {missing_prov}"
        )

    # allowed_workflow_task_types — each must be in SCHEDULABLE_TASK_TYPES
    allowed_types = data.get("allowed_workflow_task_types", [])
    if not isinstance(allowed_types, list):
        raise ValueError(
            f"Schedule at {path}: allowed_workflow_task_types must be a list"
        )
    if schedule_kind == "workflow" and not allowed_types:
        raise ValueError(
            f"Schedule at {path}: allowed_workflow_task_types must be a non-empty list"
        )
    for t in allowed_types:
        if t not in SCHEDULABLE_TASK_TYPES:
            raise ValueError(
                f"Schedule at {path}: task_type {t!r} in allowed_workflow_task_types "
                f"is not schedulable. Schedulable types: {sorted(SCHEDULABLE_TASK_TYPES)}"
            )

    # audit_requirements — must be a list
    if schedule_kind == "command":
        command_id = data.get("command_id")
        if command_id not in VALID_SCHEDULE_COMMANDS:
            raise ValueError(
                f"Schedule at {path}: command_id {command_id!r} is not schedulable. "
                f"Valid command IDs: {sorted(VALID_SCHEDULE_COMMANDS)}"
            )

        declared_command = data.get("command")
        expected_command = VALID_SCHEDULE_COMMANDS[command_id]
        if declared_command is not None and declared_command != expected_command:
            raise ValueError(
                f"Schedule at {path}: command {declared_command!r} must exactly match "
                f"registered command for {command_id!r}: {expected_command!r}"
            )

        allowed_command_ids = data.get("allowed_command_ids", [])
        if not isinstance(allowed_command_ids, list) or not allowed_command_ids:
            raise ValueError(
                f"Schedule at {path}: allowed_command_ids must be a non-empty list"
            )
        for allowed_command_id in allowed_command_ids:
            if allowed_command_id not in VALID_SCHEDULE_COMMANDS:
                raise ValueError(
                    f"Schedule at {path}: command_id {allowed_command_id!r} in "
                    "allowed_command_ids is not schedulable"
                )
        if command_id not in allowed_command_ids:
            raise ValueError(
                f"Schedule at {path}: command_id {command_id!r} must be listed in "
                f"allowed_command_ids: {allowed_command_ids}"
            )

    audit_reqs = data.get("audit_requirements", [])
    if not isinstance(audit_reqs, list):
        raise ValueError(
            f"Schedule at {path}: audit_requirements must be a list"
        )

    # max_cycles_per_day — optional non-negative integer rate ceiling
    max_cycles = data.get("max_cycles_per_day")
    if max_cycles is not None:
        if isinstance(max_cycles, bool) or not isinstance(max_cycles, int):
            raise ValueError(
                f"Schedule at {path}: max_cycles_per_day must be a non-negative integer, "
                f"got: {max_cycles!r}"
            )
        if max_cycles < 0:
            raise ValueError(
                f"Schedule at {path}: max_cycles_per_day must be non-negative, "
                f"got: {max_cycles}"
            )

    if schedule_kind == "command":
        return

    # Workflow registry cross-reference
    if check_registry:
        workflow_id = data.get("workflow_id", "")
        registry_dir = _get_workflow_registry_dir(vault_root)
        manifest_path = registry_dir / f"{workflow_id}.yaml"

        if not manifest_path.exists():
            raise ValueError(
                f"Schedule at {path}: workflow_id {workflow_id!r} does not exist "
                f"in runtime/workflows/registry/ — "
                "schedule references a non-existent workflow"
            )

        # Load manifest to check status and task_type
        try:
            manifest = _load_yaml_mapping(manifest_path)
        except ValueError as exc:
            raise ValueError(
                f"Schedule at {path}: could not parse workflow manifest "
                f"for {workflow_id!r}: {exc}"
            )

        # If schedule is enabled, workflow must be active
        if data.get("enabled") is True:
            manifest_status = manifest.get("status", "")
            if manifest_status != "active":
                raise ValueError(
                    f"Schedule at {path}: schedule is enabled but workflow "
                    f"{workflow_id!r} has status={manifest_status!r} — "
                    "only active workflows may be scheduled while enabled"
                )

        # Workflow task_type must be schedulable
        workflow_task_type = manifest.get("task_type", "")
        if workflow_task_type not in SCHEDULABLE_TASK_TYPES:
            raise ValueError(
                f"Schedule at {path}: workflow {workflow_id!r} has "
                f"task_type={workflow_task_type!r} which is not in "
                f"SCHEDULABLE_TASK_TYPES: {sorted(SCHEDULABLE_TASK_TYPES)}"
            )

        # Workflow task_type must be in allowed_workflow_task_types
        if workflow_task_type not in allowed_types:
            raise ValueError(
                f"Schedule at {path}: workflow {workflow_id!r} has "
                f"task_type={workflow_task_type!r} but this is not listed "
                f"in allowed_workflow_task_types: {allowed_types}"
            )

        # Permission ceiling must not be in NEVER_SCHEDULABLE_CEILINGS
        ceiling = manifest.get("permission_ceiling", "")
        if ceiling in NEVER_SCHEDULABLE_CEILINGS:
            raise ValueError(
                f"Schedule at {path}: workflow {workflow_id!r} has "
                f"permission_ceiling={ceiling!r} which is never schedulable"
            )


def _dict_to_schedule_intent(data: dict, path: Path) -> ScheduleIntent:
    """Convert a validated YAML dict to a ScheduleIntent dataclass instance."""
    cadence_raw = data.get("cadence", {})
    cadence = ScheduleCadence(
        type=cadence_raw.get("type", ""),
        cron_expression=cadence_raw.get("cron_expression"),
        timezone=cadence_raw.get("timezone"),
        event_type=cadence_raw.get("event_type"),
        event_source=cadence_raw.get("event_source"),
    )

    delivery_raw = data.get("delivery", {})
    delivery = ScheduleDelivery(
        primary_target=delivery_raw.get("primary_target", ""),
        vault_writeback_targets=list(delivery_raw.get("vault_writeback_targets") or []),
        external_delivery_declared=bool(delivery_raw.get("external_delivery_declared", False)),
        vault_local_only=bool(delivery_raw.get("vault_local_only", False)),
    )

    provenance_raw = data.get("provenance", {})
    provenance = ScheduleProvenance(
        created_by=provenance_raw.get("created_by", ""),
        created_at=provenance_raw.get("created_at", ""),
        rationale=provenance_raw.get("rationale", ""),
    )

    return ScheduleIntent(
        schedule_id=data["schedule_id"],
        workflow_id=data.get("workflow_id"),
        owner=data.get("owner", "operator"),
        cadence=cadence,
        trigger_source=data.get("trigger_source", ""),
        runtime_adapter_target=data["runtime_adapter_target"],
        delivery=delivery,
        approval_policy=data["approval_policy"],
        enabled=bool(data["enabled"]),
        shadow_mode=bool(data["shadow_mode"]),
        failure_behavior=data["failure_behavior"],
        audit_requirements=list(data.get("audit_requirements") or []),
        allowed_workflow_task_types=list(data.get("allowed_workflow_task_types") or []),
        provenance=provenance,
        schedule_kind=data.get("schedule_kind", "workflow"),
        command_id=data.get("command_id"),
        command=data.get("command") or VALID_SCHEDULE_COMMANDS.get(data.get("command_id")),
        allowed_command_ids=list(data.get("allowed_command_ids") or []),
        notes=data.get("notes"),
        runtime_adapter_fallback=data.get("runtime_adapter_fallback"),
        max_cycles_per_day=data.get("max_cycles_per_day"),
        _source_path=path,
    )


# ── Public API ────────────────────────────────────────────────────────────────

def load_schedule(
    schedule_id: str,
    vault_root: Optional[Path] = None,
    *,
    check_registry: bool = True,
) -> Optional[ScheduleIntent]:
    """
    Load and validate a schedule intent by ID.

    Returns a ScheduleIntent on success.
    Returns None if the schedule file does not exist.
    Raises ValueError if the file exists but fails validation.
    Raises RuntimeError if vault root cannot be detected.
    """
    if vault_root is None:
        vault_root = _detect_vault_root()

    schedules_dir = _get_schedules_dir(vault_root)
    schedule_path = schedules_dir / f"{schedule_id}.yaml"

    if not schedule_path.exists():
        return None

    data = _load_yaml_mapping(schedule_path)

    _validate_schedule(data, schedule_path, vault_root, check_registry=check_registry)
    return _dict_to_schedule_intent(data, schedule_path)


def list_schedules(
    vault_root: Optional[Path] = None,
    *,
    check_registry: bool = True,
) -> list[ScheduleIntent]:
    """
    Load and return all valid schedule intents from runtime/schedules/.

    Files named 'index.yaml' or starting with '_' are skipped.
    Invalid files are skipped with a warning to stderr.
    """
    if vault_root is None:
        vault_root = _detect_vault_root()

    schedules_dir = _get_schedules_dir(vault_root)

    if not schedules_dir.exists():
        return []

    results: list[ScheduleIntent] = []
    for path in sorted(schedules_dir.glob("*.yaml")):
        if path.name == "index.yaml" or path.name.startswith("_"):
            continue
        try:
            intent = load_schedule(path.stem, vault_root, check_registry=check_registry)
            if intent is not None:
                results.append(intent)
        except ValueError as exc:
            print(
                f"ChaseOS schedules: skipping {path.name} — {exc}",
                file=sys.stderr,
            )

    return results


def validate_all_schedules(
    vault_root: Optional[Path] = None,
) -> list[tuple[str, str]]:
    """
    Validate all schedule intent files against the workflow registry.

    Returns a list of (schedule_id, error_message) tuples for invalid schedules.
    An empty list means all schedules are valid.

    Does not raise — collects all errors and returns them.
    """
    if vault_root is None:
        vault_root = _detect_vault_root()

    schedules_dir = _get_schedules_dir(vault_root)

    if not schedules_dir.exists():
        return []

    errors: list[tuple[str, str]] = []
    for path in sorted(schedules_dir.glob("*.yaml")):
        if path.name == "index.yaml" or path.name.startswith("_"):
            continue
        try:
            data = _load_yaml_mapping(path)
            _validate_schedule(data, path, vault_root, check_registry=True)
        except ValueError as exc:
            errors.append((path.stem, str(exc)))

    return errors


# ── Enable / Disable ──────────────────────────────────────────────────────────

def _set_enabled_in_file(schedule_path: Path, enabled: bool) -> bool:
    """
    Set the 'enabled:' field in a schedule YAML file in-place.

    Uses regex replacement to preserve comments and formatting.
    Returns True if the value changed, False if it was already the target value.
    Raises ValueError if the enabled: line is not found or the file is not valid.
    """
    content = schedule_path.read_text(encoding="utf-8")

    target_str = "true" if enabled else "false"
    opposite_str = "false" if enabled else "true"

    # Pattern: match "enabled: true" or "enabled: false" as a standalone line
    pattern = re.compile(
        r"^(enabled:\s*)" + re.escape(opposite_str) + r"(\s*)$",
        re.MULTILINE,
    )
    match = pattern.search(content)
    if not match:
        # Already the target value — check it's present at all
        already_pattern = re.compile(
            r"^enabled:\s*" + re.escape(target_str) + r"\s*$",
            re.MULTILINE,
        )
        if already_pattern.search(content):
            return False  # No change needed
        raise ValueError(
            f"Could not find 'enabled: true/false' line in {schedule_path}. "
            "File may be malformed."
        )

    new_content = pattern.sub(r"\g<1>" + target_str + r"\g<2>", content)
    schedule_path.write_text(new_content, encoding="utf-8")
    return True


def _write_state_change_log(
    schedule_id: str,
    action: str,
    previous_value: bool,
    new_value: bool,
    vault_root: Path,
) -> None:
    """
    Append a state change record to the schedule state log.
    Log is at 07_LOGS/Schedule-State/schedule_state_log.jsonl.
    """
    log_path = _get_state_log_path(vault_root)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    record = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "schedule_id": schedule_id,
        "action": action,
        "previous_enabled": previous_value,
        "new_enabled": new_value,
    }
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def _regenerate_index(vault_root: Path) -> None:
    """
    Regenerate runtime/schedules/index.yaml from all individual schedule files.
    Uses lenient loading (no registry cross-reference) to avoid blocking on
    schedule files that reference workflows that may be in flux.
    """
    schedules = list_schedules(vault_root, check_registry=False)
    schedules_dir = _get_schedules_dir(vault_root)
    index_path = schedules_dir / "index.yaml"

    entries = []
    for s in schedules:
        entry = {
            "schedule_id": s.schedule_id,
            "schedule_kind": s.schedule_kind,
            "workflow_id": s.workflow_id,
            "command_id": s.command_id,
            "cadence_type": s.cadence.type,
            "enabled": s.enabled,
            "shadow_mode": s.shadow_mode,
            "runtime_adapter_target": s.runtime_adapter_target,
            "created_at": s.provenance.created_at,
        }
        if s.cadence.cron_expression:
            entry["cron_expression"] = s.cadence.cron_expression
        if s.cadence.timezone:
            entry["timezone"] = s.cadence.timezone
        if s.runtime_adapter_fallback is not None:
            entry["runtime_adapter_fallback"] = s.runtime_adapter_fallback
        if s.max_cycles_per_day is not None:
            entry["max_cycles_per_day"] = s.max_cycles_per_day
        entries.append(entry)

    def _schedule_index_sort_key(entry: dict[str, Any]) -> tuple[int, int, str]:
        cron_expression = str(entry.get("cron_expression") or "")
        parts = cron_expression.split()
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            return (int(parts[1]), int(parts[0]), str(entry.get("schedule_id") or ""))
        return (99, 99, str(entry.get("schedule_id") or ""))

    entries.sort(key=_schedule_index_sort_key)

    index_data = {
        "schema_version": "1.0",
        "regenerated_at": datetime.now(timezone.utc).isoformat(),
        "schedules": entries,
    }

    # Write YAML with a header comment preserved
    header = (
        "# runtime/schedules/index.yaml\n"
        "# Auto-generated summary — do not edit manually.\n"
        "# Source of truth: individual schedule intent files (*.yaml).\n"
        "# Regenerated by: chaseos schedule enable/disable\n"
        "#\n"
    )
    if yaml is not None:
        body = yaml.dump(index_data, default_flow_style=False, sort_keys=False, allow_unicode=True)
    else:
        body = _dump_simple_yaml(index_data) + "\n"
    index_path.write_text(header + body, encoding="utf-8")


def enable_schedule(
    schedule_id: str,
    vault_root: Optional[Path] = None,
) -> bool:
    """
    Enable a registered schedule intent.

    Returns True if the schedule was enabled (state changed).
    Returns False if the schedule was already enabled.
    Raises ValueError if the schedule does not exist or the file is malformed.
    Raises RuntimeError if vault root cannot be detected.
    """
    if vault_root is None:
        vault_root = _detect_vault_root()

    schedules_dir = _get_schedules_dir(vault_root)
    schedule_path = schedules_dir / f"{schedule_id}.yaml"

    if not schedule_path.exists():
        raise ValueError(
            f"Schedule {schedule_id!r} not found in runtime/schedules/ — "
            "cannot enable a non-existent schedule"
        )

    changed = _set_enabled_in_file(schedule_path, enabled=True)
    if changed:
        _write_state_change_log(schedule_id, "enable", False, True, vault_root)
        _regenerate_index(vault_root)
    return changed


def disable_schedule(
    schedule_id: str,
    vault_root: Optional[Path] = None,
) -> bool:
    """
    Disable a registered schedule intent.

    Returns True if the schedule was disabled (state changed).
    Returns False if the schedule was already disabled.
    Raises ValueError if the schedule does not exist or the file is malformed.
    Raises RuntimeError if vault root cannot be detected.
    """
    if vault_root is None:
        vault_root = _detect_vault_root()

    schedules_dir = _get_schedules_dir(vault_root)
    schedule_path = schedules_dir / f"{schedule_id}.yaml"

    if not schedule_path.exists():
        raise ValueError(
            f"Schedule {schedule_id!r} not found in runtime/schedules/ — "
            "cannot disable a non-existent schedule"
        )

    changed = _set_enabled_in_file(schedule_path, enabled=False)
    if changed:
        _write_state_change_log(schedule_id, "disable", True, False, vault_root)
        _regenerate_index(vault_root)
    return changed


# ── Adapter Export ─────────────────────────────────────────────────────────────

def export_schedules_for_adapter(
    adapter_id: str,
    vault_root: Optional[Path] = None,
    *,
    enabled_only: bool = True,
) -> list[dict]:
    """
    Export a compact, adapter-ready view of schedule intents targeting a specific adapter.

    This is the bridge function for runtime adapters (e.g. OpenClaw) to consume
    ChaseOS-native schedule intent. Adapters call this to discover what schedules
    they are responsible for executing and what command to invoke at each trigger time.

    Parameters
    ----------
    adapter_id : str
        The adapter to export for (e.g. "openclaw"). Only schedules with
        runtime_adapter_target matching this value are returned.
    vault_root : Path, optional
        Vault root override. Auto-detected if not provided.
    enabled_only : bool
        If True (default), only return enabled schedules. Set False to include
        disabled schedules (useful for audit/inspection).

    Returns
    -------
    list[dict]
        Each dict contains:
          schedule_id, schedule_kind, workflow_id, command_id, cadence_type, cron_expression, timezone,
          enabled, shadow_mode, command, approval_policy, failure_behavior,
          vault_writeback_targets, audit_requirements
        The 'command' field is the exact chaseos CLI invocation to run.

    Notes
    -----
    - schedule_id uniqueness is enforced by the file-per-schedule storage model.
    - Two schedules targeting the same workflow_id or command_id for the same
      adapter are configuration errors (would cause double-execution). This
      function detects and raises ValueError on duplicates.
    """
    if vault_root is None:
        vault_root = _detect_vault_root()

    all_schedules = list_schedules(vault_root, check_registry=False)

    # Primary: this adapter is the designated executor
    primary_schedules = [
        s for s in all_schedules
        if s.runtime_adapter_target == adapter_id
    ]

    # Fallback: this adapter is the declared fallback executor
    fallback_schedules = [
        s for s in all_schedules
        if s.runtime_adapter_target != adapter_id
        and s.runtime_adapter_fallback == adapter_id
    ]

    # Apply enabled filter to each group independently
    if enabled_only:
        primary_schedules = [s for s in primary_schedules if s.enabled]
        fallback_schedules = [s for s in fallback_schedules if s.enabled]

    # Combine: primary first (is_fallback=False), then fallback (is_fallback=True)
    adapter_entries: list[tuple[ScheduleIntent, bool]] = (
        [(s, False) for s in primary_schedules]
        + [(s, True) for s in fallback_schedules]
    )

    # Duplicate detection: same (adapter, workflow/command) pair more than once
    seen_workflows: dict[str, str] = {}  # workflow_id -> schedule_id
    seen_commands: dict[str, str] = {}  # command_id -> schedule_id
    for s, _ in adapter_entries:
        if s.schedule_kind == "command":
            command_id = s.command_id or ""
            if command_id in seen_commands:
                raise ValueError(
                    f"Duplicate schedule detected: command_id '{command_id}' is targeted "
                    f"by both '{seen_commands[command_id]}' and '{s.schedule_id}' "
                    f"for adapter '{adapter_id}'. Only one schedule per command per adapter "
                    f"is permitted to prevent double-execution."
                )
            seen_commands[command_id] = s.schedule_id
            continue

        workflow_id = s.workflow_id or ""
        if workflow_id in seen_workflows:
            raise ValueError(
                f"Duplicate schedule detected: workflow_id '{workflow_id}' is targeted "
                f"by both '{seen_workflows[workflow_id]}' and '{s.schedule_id}' "
                f"for adapter '{adapter_id}'. Only one schedule per workflow per adapter "
                f"is permitted to prevent double-execution."
            )
        seen_workflows[workflow_id] = s.schedule_id

    def _build_command(schedule: ScheduleIntent) -> str:
        if schedule.schedule_kind == "command":
            if schedule.command:
                return schedule.command
            if schedule.command_id:
                return VALID_SCHEDULE_COMMANDS[schedule.command_id]
            raise ValueError(
                f"Schedule {schedule.schedule_id!r} is a command schedule without command_id"
            )

        command = f"chaseos run {schedule.workflow_id}"
        manifest_path = _get_workflow_registry_dir(vault_root) / f"{schedule.workflow_id}.yaml"
        if not manifest_path.exists():
            return command
        try:
            manifest = _load_yaml_mapping(manifest_path)
        except ValueError:
            return command

        coordination = manifest.get("coordination_requirements") or {}
        if coordination.get("coordination_sensitive") is not True:
            return command

        workflow_adapter = manifest.get("runtime_adapter") or schedule.runtime_adapter_target
        if workflow_adapter:
            command += f" --adapter {workflow_adapter}"
        if coordination.get("via"):
            command += f" --coordination-via {coordination['via']}"
        return command

    result = []
    for s, is_fallback in sorted(adapter_entries, key=lambda x: x[0].schedule_id):
        entry: dict = {
            "schedule_id": s.schedule_id,
            "schedule_kind": s.schedule_kind,
            "workflow_id": s.workflow_id,
            "command_id": s.command_id,
            "cadence_type": s.cadence.type,
            "cron_expression": s.cadence.cron_expression,
            "timezone": s.cadence.timezone,
            "enabled": s.enabled,
            "shadow_mode": s.shadow_mode,
            "command": _build_command(s),
            "approval_policy": s.approval_policy,
            "failure_behavior": s.failure_behavior,
            "vault_writeback_targets": list(s.delivery.vault_writeback_targets),
            "audit_requirements": list(s.audit_requirements),
            "is_fallback": is_fallback,
        }
        result.append(entry)

    return result
