"""
runtime/common/simple_yaml.py — dependency-free YAML subset parser (Core util).

Parses the limited YAML shape used by ChaseOS manifests/configs without requiring
PyYAML: nested mappings, lists, mapping-style list items, simple scalars, and
literal/folded block strings. PyYAML remains preferred when installed; this is the
stdlib fallback so Core modules and adapters never hard-depend on PyYAML or on a
parser living in an excluded module.

Public API: parse_simple_yaml(text) -> dict. `_parse_simple_yaml` is kept as an
alias for drop-in compatibility with callers that used the original aor.registry copy.
"""

from __future__ import annotations

from typing import Any


def coerce_scalar(value: str) -> Any:
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


def parse_simple_yaml(text: str) -> dict[str, Any]:
    """Parse the limited workflow-manifest YAML shape without PyYAML.

    Supports only the subset used by ChaseOS manifests: nested mappings, lists,
    mapping-style list items, simple scalars, and literal/folded block strings.
    PyYAML remains preferred when installed.
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
            mapping[key] = coerce_scalar(value)
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
                    item[item_key] = coerce_scalar(item_val)
                    index += 1
                next_index = _next_content(index)
                if next_index < len(lines) and _indent(lines[next_index]) == indent + 2:
                    continuation, index = _parse_mapping(next_index, indent + 2)
                    item.update(continuation)
                items.append(item)
                continue
            items.append(coerce_scalar(item_value))
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


# Backwards-compatible aliases (original private names from runtime.aor.registry).
_coerce_scalar = coerce_scalar
_parse_simple_yaml = parse_simple_yaml
