"""Workspace Mode Layer profile loading."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .inference import infer_workspace_mode
from .models import WorkspaceModeProfile, build_unknown_profile

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - exercised only without PyYAML
    yaml = None


class WorkspaceModeLoadError(ValueError):
    """Raised when a workspace mode profile cannot be loaded."""


def _coerce_scalar(value: str) -> Any:
    value = value.strip()
    if value == "[]":
        return []
    if value == "{}":
        return {}
    if value in {"", "null", "None"}:
        return ""
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    return value


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    """Parse the limited profile YAML shape used by WML tests/templates."""

    result: dict[str, Any] = {}
    current_key: str | None = None
    current_map_key: str | None = None
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        raw = lines[index].rstrip()
        stripped = raw.strip()
        index += 1
        if not stripped or stripped.startswith("#") or stripped == "---":
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if indent == 0:
            if ":" not in stripped:
                raise WorkspaceModeLoadError(f"unsupported YAML line: {raw}")
            key, value = stripped.split(":", 1)
            key = key.strip()
            value = value.strip()
            current_key = key
            current_map_key = None
            if value in {">", "|"}:
                block_lines: list[str] = []
                while index < len(lines):
                    block_raw = lines[index].rstrip()
                    block_indent = len(block_raw) - len(block_raw.lstrip(" "))
                    if block_raw.strip() and block_indent == 0:
                        break
                    block_lines.append(block_raw[2:] if block_raw.startswith("  ") else block_raw)
                    index += 1
                result[key] = "\n".join(block_lines).strip()
            elif value:
                result[key] = _coerce_scalar(value)
            else:
                result[key] = []
            continue
        if current_key is None:
            raise WorkspaceModeLoadError(f"nested YAML without parent: {raw}")
        if stripped.startswith("- "):
            if not isinstance(result.get(current_key), list):
                result[current_key] = []
            result[current_key].append(_coerce_scalar(stripped[2:]))
            continue
        if ":" in stripped:
            if not isinstance(result.get(current_key), dict):
                result[current_key] = {}
            map_key, value = stripped.split(":", 1)
            map_key = map_key.strip()
            value = value.strip()
            if value == "":
                result[current_key][map_key] = {}
                current_map_key = map_key
            else:
                result[current_key][map_key] = _coerce_scalar(value)
                current_map_key = None
            continue
        if current_map_key is not None:
            result[current_key][current_map_key] = stripped
            continue
        raise WorkspaceModeLoadError(f"unsupported YAML line: {raw}")
    return result


def parse_profile_text(text: str) -> dict[str, Any]:
    if yaml is not None:
        data = yaml.safe_load(text)
    else:
        data = _parse_simple_yaml(text)
    if not isinstance(data, dict):
        raise WorkspaceModeLoadError("workspace profile did not parse as a mapping")
    return data


def extract_frontmatter(text: str) -> str:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise WorkspaceModeLoadError("markdown profile has no YAML frontmatter")
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "\n".join(lines[1:index])
    raise WorkspaceModeLoadError("markdown profile frontmatter is not closed")


def load_workspace_profile(path: str | Path) -> WorkspaceModeProfile:
    profile_path = Path(path)
    text = profile_path.read_text(encoding="utf-8")
    if profile_path.suffix.lower() in {".md", ".markdown"}:
        text = extract_frontmatter(text)
    data = parse_profile_text(text)
    return WorkspaceModeProfile.from_mapping(data)


def load_workspace_profile_from_mapping(data: Mapping[str, Any]) -> WorkspaceModeProfile:
    return WorkspaceModeProfile.from_mapping(data)


def load_workspace_profile_or_unknown(path: str | Path) -> WorkspaceModeProfile:
    try:
        return load_workspace_profile(path)
    except (OSError, ValueError):
        return build_unknown_profile(str(path))


def resolve_workspace_mode_for_path(path: str | Path, *, vault_root: str | Path | None = None) -> str:
    return infer_workspace_mode(path, vault_root=vault_root)
