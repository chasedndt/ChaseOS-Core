"""
runtime_registry.py — ChaseOS Phase 9 Runtime Registry substrate

Machine-readable registry of known runtime instances.

Registry layout:
    runtime/aor/runtime_registry/<runtime_id>/registry_entry.yaml

Public API:
    load_runtime_entry(runtime_id, vault_root=None) -> dict | None
    list_runtime_entries(vault_root=None) -> list[dict]
    register_runtime_entry(..., vault_root=None) -> dict
    transition_runtime_lifecycle(runtime_id, lifecycle_state, decision_ref, vault_root=None) -> dict
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional

try:
    import yaml  # type: ignore
except Exception:
    yaml = None


REGISTRY_ENTRY_REQUIRED_FIELDS: list[str] = [
    "runtime_id",
    "provider",
    "surface_type",
    "adapter_binding_status",
    "trust_ceiling",
    "allowed_task_families",
    "lifecycle_state",
    "initial_scope_posture",
]

VALID_LIFECYCLE_STATES: set[str] = {
    "discovered",
    "declared",
    "registered",
    "sandboxed",
    "advisory-only",
    "review-required",
    "execution-capable",
    "suspended",
    "retired",
}

VALID_TRUST_CEILINGS: set[str] = {"tier-1", "tier-2", "tier-3", "tier-4"}


def _now_utc_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _coerce_scalar(value: str) -> Any:
    value = value.strip()
    if value == "[]":
        return []
    lowered = value.lower()
    if lowered in {"null", "none"}:
        return None
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    parsed_lines: list[tuple[int, str, int]] = []
    for line_no, raw in enumerate(text.splitlines(), start=1):
        raw = raw.rstrip()
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or stripped == "---":
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        parsed_lines.append((indent, raw[indent:], line_no))

    if not parsed_lines:
        raise ValueError("Runtime registry entry is empty")

    def parse_block(index: int, indent: int) -> tuple[Any, int]:
        if index >= len(parsed_lines):
            return {}, index
        current_indent, current_text, _ = parsed_lines[index]
        if current_indent != indent:
            raise ValueError(f"Unsupported YAML indentation near line {parsed_lines[index][2]}")
        if current_text.startswith("- "):
            return parse_list(index, indent)
        return parse_mapping(index, indent)

    def consume_scalar_continuation(index: int, parent_indent: int, value: Any) -> tuple[Any, int]:
        if not isinstance(value, str):
            return value, index
        parts = [value]
        while index < len(parsed_lines):
            next_indent, next_text, _ = parsed_lines[index]
            if next_indent <= parent_indent:
                break
            if next_text.startswith("- ") or ":" in next_text:
                break
            parts.append(next_text.strip())
            index += 1
        if len(parts) == 1:
            return value, index
        return "\n".join(parts), index

    def parse_mapping(index: int, indent: int) -> tuple[dict[str, Any], int]:
        result: dict[str, Any] = {}
        while index < len(parsed_lines):
            current_indent, current_text, line_no = parsed_lines[index]
            if current_indent < indent:
                break
            if current_indent != indent or current_text.startswith("- "):
                break
            if ":" not in current_text:
                raise ValueError(f"Unsupported YAML syntax on line {line_no}: {current_text}")
            key, raw_value = current_text.split(":", 1)
            key = key.strip()
            value = raw_value.strip()
            if not key:
                raise ValueError(f"Empty mapping key on line {line_no}")
            index += 1
            if value in {"|", ">"}:
                block_lines: list[str] = []
                while index < len(parsed_lines):
                    child_indent, child_text, _ = parsed_lines[index]
                    if child_indent <= current_indent:
                        break
                    block_lines.append(child_text)
                    index += 1
                result[key] = "\n".join(block_lines).strip()
                continue
            if value != "":
                scalar, index = consume_scalar_continuation(index, current_indent, _coerce_scalar(value))
                result[key] = scalar
                continue
            if index >= len(parsed_lines):
                result[key] = []
                continue
            next_indent, next_text, _ = parsed_lines[index]
            if next_indent < current_indent or (next_indent == current_indent and not next_text.startswith("- ")):
                result[key] = []
                continue
            child_indent = next_indent
            nested, index = parse_block(index, child_indent)
            result[key] = nested
        return result, index

    def parse_list(index: int, indent: int) -> tuple[list[Any], int]:
        items: list[Any] = []
        while index < len(parsed_lines):
            current_indent, current_text, line_no = parsed_lines[index]
            if current_indent < indent:
                break
            if current_indent != indent or not current_text.startswith("- "):
                break
            payload = current_text[2:].strip()
            index += 1
            if payload == "":
                if index >= len(parsed_lines) or parsed_lines[index][0] <= current_indent:
                    items.append(None)
                    continue
                nested, index = parse_block(index, parsed_lines[index][0])
                items.append(nested)
                continue
            if ":" in payload:
                key, raw_value = payload.split(":", 1)
                item: dict[str, Any] = {key.strip(): _coerce_scalar(raw_value.strip())} if raw_value.strip() else {key.strip(): []}
                if not raw_value.strip() and index < len(parsed_lines) and parsed_lines[index][0] > current_indent:
                    nested, index = parse_block(index, parsed_lines[index][0])
                    item[key.strip()] = nested
                while index < len(parsed_lines):
                    next_indent, next_text, next_line_no = parsed_lines[index]
                    if next_indent <= current_indent:
                        break
                    if next_text.startswith("- ") and next_indent == indent:
                        break
                    child_indent = current_indent + 2
                    if next_indent != child_indent:
                        raise ValueError(f"Unsupported YAML indentation on line {next_line_no}: {next_text}")
                    if ":" not in next_text:
                        last_key = list(item.keys())[-1]
                        existing = item[last_key]
                        if isinstance(existing, str):
                            item[last_key] = f"{existing}\n{next_text.strip()}"
                            index += 1
                            continue
                        raise ValueError(f"Unsupported YAML syntax on line {next_line_no}: {next_text}")
                    child_key, child_raw_value = next_text.split(":", 1)
                    child_key = child_key.strip()
                    child_value = child_raw_value.strip()
                    index += 1
                    if child_value != "":
                        scalar, index = consume_scalar_continuation(index, child_indent, _coerce_scalar(child_value))
                        item[child_key] = scalar
                        continue
                    if index >= len(parsed_lines) or parsed_lines[index][0] <= child_indent:
                        item[child_key] = []
                        continue
                    nested, index = parse_block(index, parsed_lines[index][0])
                    item[child_key] = nested
                items.append(item)
                continue
            scalar, index = consume_scalar_continuation(index, current_indent, _coerce_scalar(payload))
            items.append(scalar)
        return items, index

    result, final_index = parse_block(0, parsed_lines[0][0])
    if final_index != len(parsed_lines):
        raise ValueError(f"Unsupported trailing YAML syntax near line {parsed_lines[final_index][2]}")
    if not isinstance(result, dict):
        raise ValueError("Runtime registry entry must parse to a dict")
    return result


def _detect_vault_root() -> Path:
    current = Path(__file__).resolve()
    for candidate in [current] + list(current.parents):
        if (candidate / "CLAUDE.md").exists():
            return candidate
    raise FileNotFoundError("Could not locate ChaseOS vault root (CLAUDE.md not found)")


def _registry_dir(vault_root: Optional[Path] = None) -> Path:
    root = Path(vault_root) if vault_root else _detect_vault_root()
    return root / "runtime" / "aor" / "runtime_registry"


def _entry_path(runtime_id: str, vault_root: Optional[Path] = None) -> Path:
    return _registry_dir(vault_root) / runtime_id / "registry_entry.yaml"


def _audit_log_path(runtime_id: str, vault_root: Optional[Path] = None) -> Path:
    return _registry_dir(vault_root) / runtime_id / "audit" / "lifecycle_log.jsonl"


def _load_yaml_file(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if yaml is not None:
        data = yaml.safe_load(text) or {}
        if not isinstance(data, dict):
            raise ValueError(f"Runtime registry entry must parse to a dict: {path}")
        return data
    return _parse_simple_yaml(text)


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)



def _emit_yaml_lines(value: Any, indent: int = 0) -> list[str]:
    prefix = " " * indent
    if isinstance(value, dict):
        lines: list[str] = []
        for key, child in value.items():
            if isinstance(child, (dict, list)):
                if isinstance(child, list) and not child:
                    lines.append(f"{prefix}{key}: []")
                else:
                    lines.append(f"{prefix}{key}:")
                    lines.extend(_emit_yaml_lines(child, indent + 2))
            else:
                lines.append(f"{prefix}{key}: {_yaml_scalar(child)}")
        return lines
    if isinstance(value, list):
        lines = []
        for item in value:
            if isinstance(item, dict):
                if not item:
                    lines.append(f"{prefix}- {{}}")
                    continue
                first = True
                for key, child in item.items():
                    marker = "- " if first else "  "
                    if isinstance(child, (dict, list)):
                        if isinstance(child, list) and not child:
                            lines.append(f"{prefix}{marker}{key}: []")
                        else:
                            lines.append(f"{prefix}{marker}{key}:")
                            lines.extend(_emit_yaml_lines(child, indent + 4))
                    else:
                        lines.append(f"{prefix}{marker}{key}: {_yaml_scalar(child)}")
                    first = False
            elif isinstance(item, list):
                if not item:
                    lines.append(f"{prefix}- []")
                else:
                    lines.append(f"{prefix}-")
                    lines.extend(_emit_yaml_lines(item, indent + 2))
            else:
                lines.append(f"{prefix}- {_yaml_scalar(item)}")
        return lines
    return [f"{prefix}{_yaml_scalar(value)}"]



def _dump_yaml_file(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if yaml is not None:
        path.write_text(
            yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        return

    path.write_text("\n".join(_emit_yaml_lines(data)) + "\n", encoding="utf-8")


def _append_lifecycle_audit(runtime_id: str, event: dict[str, Any], vault_root: Optional[Path] = None) -> None:
    path = _audit_log_path(runtime_id, vault_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


def _validate_runtime_entry(data: dict[str, Any], path: Path) -> None:
    missing = [field for field in REGISTRY_ENTRY_REQUIRED_FIELDS if field not in data]
    if missing:
        raise ValueError(f"Runtime registry entry missing required fields {missing}: {path}")

    lifecycle_state = str(data.get("lifecycle_state", "")).strip()
    if lifecycle_state not in VALID_LIFECYCLE_STATES:
        raise ValueError(f"Invalid lifecycle_state '{lifecycle_state}' in {path}")

    trust_ceiling = str(data.get("trust_ceiling", "")).strip()
    if trust_ceiling not in VALID_TRUST_CEILINGS:
        raise ValueError(f"Invalid trust_ceiling '{trust_ceiling}' in {path}")

    allowed_task_families = data.get("allowed_task_families")
    if not isinstance(allowed_task_families, list):
        raise ValueError(f"allowed_task_families must be a list in {path}")

    runtime_id = str(data.get("runtime_id", "")).strip()
    if runtime_id != path.parent.name:
        raise ValueError(
            f"runtime_id '{runtime_id}' must match registry directory '{path.parent.name}' in {path}"
        )


def load_runtime_entry(runtime_id: str, vault_root: Optional[Path] = None) -> dict[str, Any] | None:
    path = _entry_path(runtime_id, vault_root)
    if not path.exists():
        return None
    data = _load_yaml_file(path)
    _validate_runtime_entry(data, path)
    return data


def list_runtime_entries(vault_root: Optional[Path] = None) -> list[dict[str, Any]]:
    registry_dir = _registry_dir(vault_root)
    if not registry_dir.exists():
        return []

    entries: list[dict[str, Any]] = []
    for runtime_dir in sorted(p for p in registry_dir.iterdir() if p.is_dir()):
        path = runtime_dir / "registry_entry.yaml"
        if not path.exists():
            continue
        data = _load_yaml_file(path)
        _validate_runtime_entry(data, path)
        entries.append(data)
    return sorted(entries, key=lambda item: str(item.get("runtime_id", "")))


def register_runtime_entry(
    runtime_id: str,
    *,
    provider: str,
    surface_type: str,
    vault_root: Optional[Path] = None,
    trust_ceiling: str = "tier-4",
    allowed_task_families: list[str] | None = None,
    adapter_binding_status: str = "unbound",
    initial_scope_posture: str = "read-only",
    lifecycle_state: str = "declared",
    notes: str | None = None,
) -> dict[str, Any]:
    path = _entry_path(runtime_id, vault_root)
    if path.exists():
        raise ValueError(f"Runtime registry entry already exists for '{runtime_id}'")

    timestamp = _now_utc_iso()
    entry = {
        "runtime_id": runtime_id,
        "provider": provider,
        "surface_type": surface_type,
        "adapter_binding_status": adapter_binding_status,
        "trust_ceiling": trust_ceiling,
        "allowed_task_families": allowed_task_families or [],
        "lifecycle_state": lifecycle_state,
        "initial_scope_posture": initial_scope_posture,
        "policy_binding_record": None,
        "registered": timestamp,
        "last_evaluated": timestamp,
    }
    if notes:
        entry["notes"] = notes

    _validate_runtime_entry(entry, path)
    _dump_yaml_file(path, entry)
    _append_lifecycle_audit(
        runtime_id,
        {
            "timestamp": timestamp,
            "event": "registered",
            "runtime_id": runtime_id,
            "lifecycle_state": lifecycle_state,
            "provider": provider,
            "surface_type": surface_type,
        },
        vault_root,
    )
    return entry


def transition_runtime_lifecycle(
    runtime_id: str,
    lifecycle_state: str,
    *,
    decision_ref: str,
    vault_root: Optional[Path] = None,
) -> dict[str, Any]:
    path = _entry_path(runtime_id, vault_root)
    entry = load_runtime_entry(runtime_id, vault_root)
    if entry is None:
        raise ValueError(f"Runtime registry entry not found for '{runtime_id}'")
    if lifecycle_state not in VALID_LIFECYCLE_STATES:
        raise ValueError(f"Invalid lifecycle_state '{lifecycle_state}'")
    if not decision_ref.strip():
        raise ValueError("Lifecycle transitions require a non-empty decision_ref")
    if lifecycle_state == "execution-capable" and not entry.get("policy_binding_record"):
        raise ValueError("Cannot transition to execution-capable without completed policy binding")

    prior_state = str(entry.get("lifecycle_state", "")).strip()
    changed = prior_state != lifecycle_state
    timestamp = _now_utc_iso()
    entry["lifecycle_state"] = lifecycle_state
    entry["last_evaluated"] = timestamp
    _validate_runtime_entry(entry, path)
    _dump_yaml_file(path, entry)
    audit_path = _audit_log_path(runtime_id, vault_root)
    _append_lifecycle_audit(
        runtime_id,
        {
            "timestamp": timestamp,
            "event": "lifecycle_transition",
            "runtime_id": runtime_id,
            "from_state": prior_state,
            "to_state": lifecycle_state,
            "decision_ref": decision_ref,
        },
        vault_root,
    )
    return {"entry": entry, "changed": changed, "audit_path": audit_path}


def register_runtime(
    provider: str,
    surface_type: str,
    *,
    runtime_id: str | None = None,
    vault_root: Optional[Path] = None,
    trust_ceiling: str = "tier-4",
    notes: str | None = None,
) -> dict[str, Any]:
    resolved_runtime_id = runtime_id or f"{provider}-{surface_type}".replace(" ", "-").lower()
    entry = register_runtime_entry(
        resolved_runtime_id,
        provider=provider,
        surface_type=surface_type,
        vault_root=vault_root,
        trust_ceiling=trust_ceiling,
        notes=notes,
    )
    return {"entry": entry, "path": _entry_path(resolved_runtime_id, vault_root)}


def transition_lifecycle_state(
    runtime_id: str,
    lifecycle_state: str,
    *,
    decision_ref: str,
    vault_root: Optional[Path] = None,
) -> dict[str, Any]:
    return transition_runtime_lifecycle(
        runtime_id,
        lifecycle_state,
        decision_ref=decision_ref,
        vault_root=vault_root,
    )
