"""
capabilities.py — Runtime Capability Registry for the ChaseOS Agent Bus

Each runtime declares its capabilities in runtime/{runtime}/capabilities.yaml.
This module discovers and loads those manifests, building a capability registry
that the router uses to answer: "which runtime(s) can handle this task type?"

Design decisions:
  - Discovery is filesystem-based: scan runtime/ for directories with capabilities.yaml.
    Adding a new runtime = adding capabilities.yaml. No code changes needed.
  - Storage and packet/event schemas are runtime-generic. Runtime validity is enforced
    by this capability registry plus bus API checks rather than by Hermes/OpenClaw-only
    schema constraints.
  - Fail-closed: malformed or missing manifests raise CapabilityError before routing.
  - Priority: "primary" > "secondary". Router recommends the highest-priority
    eligible runtime that is not stale.

Public API:
    RuntimeCapability, RuntimeCapabilities
    load_runtime_capabilities(runtime_name, vault_root) -> RuntimeCapabilities
    load_all_capabilities(vault_root) -> dict[str, RuntimeCapabilities]
    get_eligible_runtimes(task_type, vault_root) -> list[str]
    discover_runtime_names(vault_root) -> list[str]
    CapabilityError
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except Exception:
    yaml = None


class CapabilityError(Exception):
    """Raised when a runtime capability manifest is missing or malformed."""


def _coerce_scalar(value: str) -> Any:
    value = value.strip()
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    try:
        return int(value)
    except ValueError:
        return value


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    lines = text.splitlines()
    i = 0
    saw_content = False
    while i < len(lines):
        raw = lines[i].rstrip()
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        saw_content = True
        if raw.startswith(" ") or ":" not in stripped:
            raise CapabilityError(f"Unsupported YAML syntax on line {i + 1}: {raw}")
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise CapabilityError(f"Empty mapping key on line {i + 1}")
        if value == "":
            nested: dict[str, Any] = {}
            items: list[Any] = []
            i += 1
            saw_child = False
            while i < len(lines):
                child = lines[i].rstrip()
                child_stripped = child.strip()
                if not child_stripped or child_stripped.startswith("#"):
                    i += 1
                    continue
                if not child.startswith("  "):
                    break
                saw_child = True
                if child_stripped.startswith("- "):
                    item_value = child_stripped[2:].strip()
                    if not item_value:
                        raise CapabilityError(f"Empty list item on line {i + 1}")
                    if ":" in item_value:
                        item_dict: dict[str, Any] = {}
                        item_key, item_val = item_value.split(":", 1)
                        item_key = item_key.strip()
                        if not item_key:
                            raise CapabilityError(f"Empty list-item key on line {i + 1}")
                        item_dict[item_key] = _coerce_scalar(item_val.strip())
                        i += 1
                        while i < len(lines):
                            grandchild = lines[i].rstrip()
                            grandchild_stripped = grandchild.strip()
                            if not grandchild_stripped or grandchild_stripped.startswith("#"):
                                i += 1
                                continue
                            if not grandchild.startswith("    "):
                                break
                            if ":" not in grandchild_stripped:
                                raise CapabilityError(f"Unsupported YAML syntax on line {i + 1}: {grandchild}")
                            subkey, subvalue = grandchild_stripped.split(":", 1)
                            subkey = subkey.strip()
                            if not subkey:
                                raise CapabilityError(f"Empty mapping key on line {i + 1}")
                            item_dict[subkey] = _coerce_scalar(subvalue.strip())
                            i += 1
                        items.append(item_dict)
                        continue
                    items.append(_coerce_scalar(item_value))
                    i += 1
                    continue
                if ":" not in child_stripped:
                    raise CapabilityError(f"Unsupported YAML syntax on line {i + 1}: {child}")
                subkey, subvalue = child_stripped.split(":", 1)
                subkey = subkey.strip()
                if not subkey:
                    raise CapabilityError(f"Empty mapping key on line {i + 1}")
                if subvalue.strip() == "":
                    i += 1
                    nested_items: list[Any] = []
                    saw_nested_child = False
                    while i < len(lines):
                        grandchild = lines[i].rstrip()
                        grandchild_stripped = grandchild.strip()
                        if not grandchild_stripped or grandchild_stripped.startswith("#"):
                            i += 1
                            continue
                        if not grandchild.startswith("    "):
                            break
                        saw_nested_child = True
                        if not grandchild_stripped.startswith("- "):
                            raise CapabilityError(f"Unsupported YAML syntax on line {i + 1}: {grandchild}")
                        nested_item_value = grandchild_stripped[2:].strip()
                        if not nested_item_value:
                            raise CapabilityError(f"Empty list item on line {i + 1}")
                        nested_items.append(_coerce_scalar(nested_item_value))
                        i += 1
                    if not saw_nested_child:
                        raise CapabilityError(f"Expected indented block after '{subkey}:'")
                    nested[subkey] = nested_items
                    continue
                nested[subkey] = _coerce_scalar(subvalue.strip())
                i += 1
            if not saw_child:
                raise CapabilityError(f"Expected indented block after '{key}:'")
            if items and nested:
                raise CapabilityError(f"Mixed list/mapping block not supported for '{key}'")
            result[key] = items if items else nested
            continue
        result[key] = _coerce_scalar(value)
        i += 1
    if not saw_content:
        raise CapabilityError("Capability manifest is empty")
    return result


# Priority ordering — lower index = higher priority
_PRIORITY_ORDER: list[str] = ["primary", "secondary", "tertiary"]


@dataclass(frozen=True)
class RuntimeCapability:
    """A single task-type capability declared by a runtime."""
    task_type: str
    priority: str = "primary"
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.task_type:
            raise CapabilityError("RuntimeCapability.task_type must be non-empty")
        if self.priority not in _PRIORITY_ORDER:
            raise CapabilityError(
                f"RuntimeCapability.priority '{self.priority}' invalid; "
                f"must be one of {_PRIORITY_ORDER}"
            )

    @property
    def priority_rank(self) -> int:
        """Lower rank = higher priority (0 = primary)."""
        try:
            return _PRIORITY_ORDER.index(self.priority)
        except ValueError:
            return 99


@dataclass(frozen=True)
class RuntimeIdentity:
    """Resolved runtime identity for canonical bus storage and instance-aware surfaces."""

    input_name: str
    bus_name: str
    runtime_name: str
    matched_as: str
    runtime_instance_id_hint: str | None = None


@dataclass
class RuntimeCapabilities:
    """Full capability declaration for a single runtime."""
    runtime_name: str
    bus_name: str
    display_name: str
    description: str
    personal_runtime_name: str = ""
    retained_runtime_name: str = ""
    legacy_personal_runtime_names: list[str] = field(default_factory=list)
    handles: list[RuntimeCapability] = field(default_factory=list)
    max_concurrent_tasks: int = 1
    heartbeat_stale_seconds: int = 900
    priority_ceiling: str = "normal"

    def can_handle(self, task_type: str) -> bool:
        return any(c.task_type == task_type for c in self.handles)

    def priority_for(self, task_type: str) -> int:
        """Return priority rank for task_type. Returns 99 if not handled."""
        for cap in self.handles:
            if cap.task_type == task_type:
                return cap.priority_rank
        return 99


def discover_runtime_names(vault_root: str | Path) -> list[str]:
    """
    Return list of runtime names that have a capabilities.yaml under runtime/.
    Sorted alphabetically.
    """
    root = Path(vault_root)
    runtime_dir = root / "runtime"
    if not runtime_dir.is_dir():
        return []
    names: list[str] = []
    for entry in runtime_dir.iterdir():
        if entry.is_dir() and (entry / "capabilities.yaml").exists():
            names.append(entry.name)
    return sorted(names)


def load_runtime_capabilities(runtime_name: str, vault_root: str | Path) -> RuntimeCapabilities:
    """
    Load capabilities.yaml for the given runtime.
    Raises CapabilityError if file is missing or malformed.
    """
    config_path = Path(vault_root) / "runtime" / runtime_name / "capabilities.yaml"
    if not config_path.exists():
        raise CapabilityError(
            f"No capabilities.yaml found for runtime '{runtime_name}' at {config_path}. "
            "Create the file to register this runtime with the capability router."
        )

    try:
        text = config_path.read_text(encoding="utf-8")
        if yaml is not None:
            raw = yaml.safe_load(text)
        else:
            raw = _parse_simple_yaml(text)
    except Exception as exc:
        raise CapabilityError(
            f"Failed to parse capabilities.yaml for '{runtime_name}': {exc}"
        ) from exc

    if not isinstance(raw, dict):
        raise CapabilityError(
            f"capabilities.yaml for '{runtime_name}' must be a YAML mapping"
        )

    bus_name = raw.get("bus_name") or raw.get("runtime") or runtime_name
    display_name = raw.get("display_name") or bus_name
    description = raw.get("description") or ""

    handles: list[RuntimeCapability] = []
    for i, entry in enumerate(raw.get("handles", []) or []):
        if isinstance(entry, str):
            handles.append(RuntimeCapability(task_type=entry))
        elif isinstance(entry, dict):
            task_type = entry.get("task_type") or entry.get("type") or ""
            if not task_type:
                raise CapabilityError(
                    f"capabilities.yaml for '{runtime_name}': "
                    f"handles[{i}] missing 'task_type' field"
                )
            handles.append(RuntimeCapability(
                task_type=task_type,
                priority=str(entry.get("priority", "primary")),
                notes=str(entry.get("notes", "")),
            ))
        else:
            raise CapabilityError(
                f"capabilities.yaml for '{runtime_name}': "
                f"handles[{i}] must be a string or mapping"
            )

    return RuntimeCapabilities(
        runtime_name=runtime_name,
        bus_name=str(bus_name),
        display_name=str(display_name),
        description=str(description),
        personal_runtime_name=str(raw.get("personal_runtime_name") or ""),
        retained_runtime_name=str(raw.get("retained_runtime_name") or ""),
        legacy_personal_runtime_names=[
            str(item) for item in (raw.get("legacy_personal_runtime_names") or []) if str(item).strip()
        ],
        handles=handles,
        max_concurrent_tasks=int(raw.get("max_concurrent_tasks", 1)),
        heartbeat_stale_seconds=int(raw.get("heartbeat_stale_seconds", 900)),
        priority_ceiling=str(raw.get("priority_ceiling", "normal")),
    )


def build_runtime_identity_index(vault_root: str | Path) -> dict[str, RuntimeIdentity]:
    """Build an alias index mapping runtime identity strings to canonical bus names.

    Capability manifests remain the authority for valid runtime identities. Storage
    should keep canonical bus names, while personal/retained/legacy names become
    runtime instance hints where appropriate.
    """
    index: dict[str, RuntimeIdentity] = {}
    for runtime_name, caps in load_all_capabilities(vault_root).items():
        candidates: list[tuple[str, str, str | None]] = [
            (caps.bus_name, "bus_name", None),
            (runtime_name, "runtime_name", None),
        ]
        if caps.personal_runtime_name:
            candidates.append((caps.personal_runtime_name, "personal_runtime_name", caps.personal_runtime_name))
        if caps.retained_runtime_name:
            candidates.append((caps.retained_runtime_name, "retained_runtime_name", caps.retained_runtime_name))
        for legacy_name in caps.legacy_personal_runtime_names:
            candidates.append((legacy_name, "legacy_personal_runtime_name", legacy_name))

        for name, matched_as, instance_hint in candidates:
            key = str(name).strip()
            if not key:
                continue
            new_identity = RuntimeIdentity(
                input_name=key,
                bus_name=caps.bus_name,
                runtime_name=runtime_name,
                matched_as=matched_as,
                runtime_instance_id_hint=instance_hint,
            )
            existing = index.get(key)
            if existing is not None:
                if existing.bus_name != new_identity.bus_name or existing.runtime_name != new_identity.runtime_name:
                    raise CapabilityError(
                        "Runtime identity collision for "
                        f"'{key}': {existing.runtime_name}/{existing.bus_name} and "
                        f"{new_identity.runtime_name}/{new_identity.bus_name}"
                    )
                continue
            index[key] = new_identity
    return index


def resolve_runtime_identity(
    vault_root: str | Path,
    name: str,
    *,
    allow_instance_alias: bool = True,
) -> RuntimeIdentity:
    """Resolve a runtime bus name, runtime directory name, or declared alias.

    Raises ValueError for unknown identities so bus callers fail closed instead
    of silently storing non-canonical runtime names.
    """
    requested = str(name or "").strip()
    if not requested:
        raise ValueError("Runtime identity must be non-empty")
    index = build_runtime_identity_index(vault_root)
    identity = index.get(requested)
    if identity is None:
        raise ValueError(
            f"Unknown runtime identity: {requested}. Known: {sorted(index)}"
        )
    if not allow_instance_alias and identity.matched_as in {
        "personal_runtime_name",
        "retained_runtime_name",
        "legacy_personal_runtime_name",
    }:
        raise ValueError(f"Runtime identity aliases are not accepted here: {requested}")
    return identity


def load_all_capabilities(vault_root: str | Path) -> dict[str, RuntimeCapabilities]:
    """
    Load capabilities for all registered runtimes (those with capabilities.yaml).
    Returns dict keyed by runtime_name. Skips runtimes with malformed manifests
    and raises CapabilityError only if ALL runtimes fail.
    """
    names = discover_runtime_names(vault_root)
    if not names:
        return {}

    result: dict[str, RuntimeCapabilities] = {}
    errors: list[str] = []
    for name in names:
        try:
            result[name] = load_runtime_capabilities(name, vault_root)
        except CapabilityError as exc:
            errors.append(f"  {name}: {exc}")

    if errors and not result:
        raise CapabilityError(
            "All runtime capability manifests failed to load:\n" + "\n".join(errors)
        )
    return result


def get_eligible_runtimes(task_type: str, vault_root: str | Path) -> list[str]:
    """
    Return list of runtime names that declare they can handle task_type.
    Sorted by priority (primary before secondary) then alphabetically.

    Returns empty list if no runtime can handle the task type.
    Each entry is the runtime's bus_name (e.g. "OpenClaw", "Hermes").
    """
    all_caps = load_all_capabilities(vault_root)
    eligible: list[tuple[int, str, str]] = []  # (priority_rank, bus_name, runtime_name)
    for runtime_name, caps in all_caps.items():
        if caps.can_handle(task_type):
            rank = caps.priority_for(task_type)
            eligible.append((rank, caps.bus_name, runtime_name))
    eligible.sort(key=lambda x: (x[0], x[1]))
    return [bus_name for _, bus_name, _ in eligible]
