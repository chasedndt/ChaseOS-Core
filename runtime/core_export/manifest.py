"""Manifest loading for ChaseOS Core export dry runs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except Exception:
    yaml = None


class CoreExportManifestError(ValueError):
    """Raised when a Core export manifest is malformed."""


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
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    """Parse the limited Core export manifest YAML shape without PyYAML."""

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
                raise ValueError(f"unsupported indentation on line {index + 1}: {raw}")
            if stripped.startswith("- "):
                break
            if ":" not in stripped:
                raise ValueError(f"unsupported syntax on line {index + 1}: {raw}")
            key, value = stripped.split(":", 1)
            key = key.strip()
            value = value.strip()
            if not key:
                raise ValueError(f"empty mapping key on line {index + 1}")
            saw_item = True
            if value == "":
                child_index = _next_content(index + 1)
                if child_index >= len(lines) or _indent(lines[child_index]) <= current_indent:
                    mapping[key] = {}
                    index += 1
                else:
                    mapping[key], index = _parse_node(child_index, _indent(lines[child_index]))
                continue
            mapping[key] = _coerce_scalar(value)
            index += 1
        if not saw_item:
            raise ValueError(f"expected mapping block at line {index + 1}")
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
                raise ValueError(f"unsupported indentation on line {index + 1}: {raw}")
            if not stripped.startswith("- "):
                break
            item_value = stripped[2:].strip()
            if not item_value:
                raise ValueError(f"empty list item on line {index + 1}")
            if ":" in item_value:
                item_key, item_val = item_value.split(":", 1)
                item_key = item_key.strip()
                item_val = item_val.strip()
                if not item_key:
                    raise ValueError(f"empty list-item key on line {index + 1}")
                item: dict[str, Any] = {item_key: _coerce_scalar(item_val) if item_val else {}}
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
        if lines[index].strip().startswith("- "):
            return _parse_sequence(index, indent)
        return _parse_mapping(index, indent)

    start = _next_content(0)
    if start >= len(lines):
        raise ValueError("manifest is empty")
    data, end_index = _parse_node(start, _indent(lines[start]))
    end_index = _next_content(end_index)
    if end_index < len(lines):
        raise ValueError(f"unsupported syntax on line {end_index + 1}: {lines[end_index].rstrip()}")
    if not isinstance(data, dict):
        raise ValueError("manifest did not parse as a mapping")
    return data


def load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise CoreExportManifestError(f"manifest not found: {path}")
    text = path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text) if yaml is not None else _parse_simple_yaml(text)
    except Exception as exc:
        raise CoreExportManifestError(f"manifest YAML parse failed: {exc}") from exc
    data = data or {}
    if not isinstance(data, dict):
        raise CoreExportManifestError("manifest must be a mapping")
    if data.get("mode") != "allowlist-only":
        raise CoreExportManifestError("manifest mode must be allowlist-only")
    include = data.get("include") or []
    if not isinstance(include, list):
        raise CoreExportManifestError("manifest include must be a list")
    for index, item in enumerate(include):
        if not isinstance(item, dict):
            raise CoreExportManifestError(f"include[{index}] must be a mapping")
        if not item.get("source") or not item.get("target"):
            raise CoreExportManifestError(f"include[{index}] requires source and target")
    exclude = data.get("exclude_always") or []
    if not isinstance(exclude, list):
        raise CoreExportManifestError("manifest exclude_always must be a list")
    return data
