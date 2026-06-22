"""
registry.py — ChaseOS AOR Phase 9

Workflow Registry loader and validator.

The Workflow Registry is the canonical manifest store for all AOR workflows.
No workflow runs without a registry entry. The registry directory is:
    runtime/workflows/registry/

Each manifest is a YAML file named <workflow_id>.yaml.
Manifest files beginning with "_" are schema/meta files and are skipped.

Public API:
    load_manifest(workflow_id, vault_root=None) -> dict | None
    list_manifests(vault_root=None) -> list[dict]

Internal:
    _get_registry_dir(vault_root) -> Path
    _validate_manifest(data, path) -> None  (raises ValueError if invalid)
    _detect_vault_root() -> Path
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Any

from .path_policy import (
    AORPathPolicyError,
    validate_repo_scope,
    validate_vault_relative_path_list,
)

try:
    import yaml  # type: ignore
except Exception:
    yaml = None


# ── Required fields for a valid workflow manifest ─────────────────────────────

MANIFEST_REQUIRED_FIELDS: list[str] = [
    "id",
    "name",
    "version",
    "description",
    "task_type",
    "role_card",
    "trigger_type",
    "owner",
    "status",
    "permission_ceiling",
    "writeback_targets",
    "failure_behavior",
]

VALID_TRIGGER_TYPES: set[str] = {"manual", "scheduled", "event"}
VALID_STATUSES: set[str] = {"active", "draft", "disabled", "deprecated"}
VALID_FAILURE_BEHAVIORS: set[str] = {"escalate", "log_and_continue", "abort"}


def _coerce_scalar(value: str) -> Any:
    value = value.strip()
    if value == "[]":
        return []
    if value == "{}":
        return {}
    if value.lower() in {"null", "none", "~"}:
        return None
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    """Parse the limited workflow-manifest YAML shape without PyYAML.

    This fallback intentionally supports only the subset used by ChaseOS workflow
    manifests: nested mappings, lists, mapping-style list items, simple scalars,
    and literal/folded block strings. PyYAML remains preferred when installed.
    """
    lines = text.splitlines()

    def _indent(line: str) -> int:
        return len(line) - len(line.lstrip(" "))

    def _next_content(index: int) -> int:
        while index < len(lines):
            stripped = lines[index].strip()
            if stripped and not stripped.startswith("#") and stripped != "---":
                return index
            index += 1
        return index

    def _parse_block_scalar(index: int, parent_indent: int) -> tuple[str, int]:
        block_lines: list[str] = []
        index += 1
        while index < len(lines):
            raw = lines[index].rstrip("\n")
            stripped = raw.strip()
            if not stripped:
                block_lines.append("")
                index += 1
                continue
            if _indent(raw) <= parent_indent:
                break
            block_lines.append(raw[parent_indent + 2 :])
            index += 1
        return "\n".join(block_lines).strip(), index

    def _parse_mapping(index: int, indent: int) -> tuple[dict[str, Any], int]:
        mapping: dict[str, Any] = {}
        saw_item = False
        while True:
            index = _next_content(index)
            if index >= len(lines):
                break
            raw = lines[index].rstrip()
            current_indent = _indent(raw)
            stripped = raw.strip()
            if current_indent < indent:
                break
            if current_indent > indent:
                raise ValueError(f"Unsupported YAML indentation on line {index + 1}: {raw}")
            if stripped.startswith("- "):
                break
            if ":" not in stripped:
                raise ValueError(f"Unsupported YAML syntax on line {index + 1}: {raw}")
            key, value = stripped.split(":", 1)
            key = key.strip()
            value = value.strip()
            if not key:
                raise ValueError(f"Empty mapping key on line {index + 1}")
            saw_item = True
            if value in {">", "|"}:
                mapping[key], index = _parse_block_scalar(index, current_indent)
                continue
            if value == "":
                child_index = _next_content(index + 1)
                if child_index >= len(lines) or _indent(lines[child_index]) <= current_indent:
                    mapping[key] = {}
                    index = index + 1
                else:
                    mapping[key], index = _parse_node(child_index, _indent(lines[child_index]))
                continue
            mapping[key] = _coerce_scalar(value)
            index += 1
        if not saw_item:
            raise ValueError(f"Expected indented mapping block at line {index + 1}")
        return mapping, index

    def _parse_sequence(index: int, indent: int) -> tuple[list[Any], int]:
        items: list[Any] = []
        while True:
            index = _next_content(index)
            if index >= len(lines):
                break
            raw = lines[index].rstrip()
            current_indent = _indent(raw)
            stripped = raw.strip()
            if current_indent < indent:
                break
            if current_indent > indent:
                raise ValueError(f"Unsupported YAML indentation on line {index + 1}: {raw}")
            if not stripped.startswith("- "):
                break
            item_value = stripped[2:].strip()
            if not item_value:
                raise ValueError(f"Empty list item on line {index + 1}")
            if ":" in item_value:
                item_key, item_val = item_value.split(":", 1)
                item_key = item_key.strip()
                item_val = item_val.strip()
                if not item_key:
                    raise ValueError(f"Empty list-item key on line {index + 1}")
                item: dict[str, Any] = {}
                if item_val in {">", "|"}:
                    item[item_key], index = _parse_block_scalar(index, current_indent)
                elif item_val == "":
                    child_index = _next_content(index + 1)
                    if child_index < len(lines) and _indent(lines[child_index]) > current_indent:
                        item[item_key], index = _parse_node(child_index, _indent(lines[child_index]))
                    else:
                        item[item_key] = {}
                        index += 1
                else:
                    item[item_key] = _coerce_scalar(item_val)
                    index += 1
                next_index = _next_content(index)
                if next_index < len(lines) and _indent(lines[next_index]) == indent + 2:
                    continuation, index = _parse_mapping(next_index, indent + 2)
                    item.update(continuation)
                items.append(item)
                continue
            items.append(_coerce_scalar(item_value))
            index += 1
        return items, index

    def _parse_node(index: int, indent: int) -> tuple[Any, int]:
        index = _next_content(index)
        if index >= len(lines):
            return {}, index
        stripped = lines[index].strip()
        if stripped.startswith("- "):
            return _parse_sequence(index, indent)
        return _parse_mapping(index, indent)

    start = _next_content(0)
    if start >= len(lines):
        raise ValueError("Manifest is empty")
    data, end_index = _parse_node(start, _indent(lines[start]))
    end_index = _next_content(end_index)
    if end_index < len(lines):
        raise ValueError(f"Unsupported YAML syntax on line {end_index + 1}: {lines[end_index].rstrip()}")
    if not isinstance(data, dict):
        raise ValueError("Manifest did not parse as a YAML mapping")
    return data


# ── Vault root detection ───────────────────────────────────────────────────────

def _detect_vault_root() -> Path:
    """
    Detect vault root by walking up from this file's location.
    This file lives at runtime/aor/registry.py, so vault root = parents[2].
    """
    here = Path(__file__).resolve()
    vault_root = here.parents[2]  # runtime/aor/registry.py → vault root
    if not (vault_root / "CLAUDE.md").exists():
        raise RuntimeError(
            f"Could not detect vault root. Expected CLAUDE.md at: {vault_root}\n"
            "Use vault_root parameter to specify the vault path explicitly."
        )
    return vault_root


def _get_registry_dir(vault_root: Path) -> Path:
    return vault_root / "runtime" / "workflows" / "registry"


# ── Validation ────────────────────────────────────────────────────────────────

def _validate_manifest(data: dict, path: Path) -> None:
    """
    Validate that a manifest dict has all required fields and valid values.
    Raises ValueError with a descriptive message if invalid.
    """
    missing = [f for f in MANIFEST_REQUIRED_FIELDS if f not in data]
    if missing:
        raise ValueError(
            f"Manifest at {path} is missing required fields: {missing}"
        )

    if data.get("trigger_type") not in VALID_TRIGGER_TYPES:
        raise ValueError(
            f"Manifest at {path}: trigger_type must be one of {VALID_TRIGGER_TYPES}, "
            f"got: {data.get('trigger_type')!r}"
        )

    if data.get("status") not in VALID_STATUSES:
        raise ValueError(
            f"Manifest at {path}: status must be one of {VALID_STATUSES}, "
            f"got: {data.get('status')!r}"
        )

    # id must match the filename stem (excluding _schema etc.)
    expected_id = path.stem
    if data.get("id") != expected_id:
        raise ValueError(
            f"Manifest at {path}: id field ({data.get('id')!r}) "
            f"must match filename stem ({expected_id!r})"
        )

    if not isinstance(data.get("writeback_targets"), list):
        raise ValueError(
            f"Manifest at {path}: writeback_targets must be a list"
        )
    if not data.get("writeback_targets"):
        raise ValueError(
            f"Manifest at {path}: writeback_targets must not be empty"
        )
    if any(not isinstance(target, str) or not target.strip() for target in data["writeback_targets"]):
        raise ValueError(
            f"Manifest at {path}: every writeback_targets entry must be a non-empty string"
        )
    try:
        validate_vault_relative_path_list(
            data["writeback_targets"],
            f"Manifest at {path}: writeback_targets",
        )
        if "required_reads" in data:
            validate_vault_relative_path_list(
                data["required_reads"],
                f"Manifest at {path}: required_reads",
                skip_runtime_placeholders=True,
            )
        validate_repo_scope(data.get("repo_scope"), f"Manifest at {path}: repo_scope")
    except AORPathPolicyError as exc:
        raise ValueError(str(exc)) from exc

    if data.get("failure_behavior") not in VALID_FAILURE_BEHAVIORS:
        raise ValueError(
            f"Manifest at {path}: failure_behavior must be one of {VALID_FAILURE_BEHAVIORS}, "
            f"got: {data.get('failure_behavior')!r}"
        )


# ── Meta-file type guard ──────────────────────────────────────────────────────

def _assert_meta_file_typed(path: Path) -> None:
    """Defense-in-depth: warn if a _-prefixed registry file lacks is_schema/is_template.

    Fail-open — a parse error or missing field prints a warning but does not
    raise, so the loader continues skipping the file normally.
    """
    import sys

    try:
        with path.open("r", encoding="utf-8") as f:
            text = f.read()
        if yaml is not None:
            data = yaml.safe_load(text)
        else:
            try:
                data = _parse_simple_yaml(text)
            except Exception:
                data = None
        if not isinstance(data, dict) or not (
            data.get("is_schema") is True or data.get("is_template") is True
        ):
            print(
                f"AOR registry: WARNING — {path.name} lacks is_schema/is_template "
                f"type frontmatter (defense-in-depth type check failed)",
                file=sys.stderr,
            )
    except Exception as exc:
        print(
            f"AOR registry: WARNING — could not verify type frontmatter for {path.name}: {exc}",
            file=sys.stderr,
        )


# ── Public API ────────────────────────────────────────────────────────────────

def load_manifest(
    workflow_id: str,
    vault_root: Optional[Path] = None,
) -> Optional[dict]:
    """
    Load and validate a workflow manifest by ID.

    Returns the manifest dict on success.
    Returns None if the manifest file does not exist.
    Raises ValueError if the manifest exists but fails validation.
    Raises RuntimeError if vault root cannot be detected.
    """
    if vault_root is None:
        vault_root = _detect_vault_root()

    registry_dir = _get_registry_dir(vault_root)
    manifest_path = registry_dir / f"{workflow_id}.yaml"

    if not manifest_path.exists():
        return None

    with manifest_path.open("r", encoding="utf-8") as f:
        text = f.read()
    if yaml is not None:
        data = yaml.safe_load(text)
    else:
        data = _parse_simple_yaml(text)

    if not isinstance(data, dict):
        raise ValueError(
            f"Manifest at {manifest_path} did not parse as a YAML mapping"
        )

    _validate_manifest(data, manifest_path)
    return data


def list_manifests(vault_root: Optional[Path] = None) -> list[dict]:
    """
    Load and return all valid workflow manifests from the registry directory.

    Files beginning with "_" are skipped (schema/meta files).
    Manifests that fail validation are skipped with a warning logged to stderr.
    """
    import sys

    if vault_root is None:
        vault_root = _detect_vault_root()

    registry_dir = _get_registry_dir(vault_root)

    if not registry_dir.exists():
        return []

    results: list[dict] = []
    for path in sorted(registry_dir.glob("*.yaml")):
        if path.name.startswith("_"):
            _assert_meta_file_typed(path)
            continue
        try:
            manifest = load_manifest(path.stem, vault_root)
            if manifest is not None:
                results.append(manifest)
        except Exception as exc:
            print(f"AOR registry: skipping {path.name} — {exc}", file=sys.stderr)

    return results
