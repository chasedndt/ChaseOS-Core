"""
task_router.py — ChaseOS AOR Phase 9

Task-Type Router — canonical classification layer for workflow task types.

Every workflow manifest declares a task_type. The router resolves that
task_type to a full TaskType definition from task_type_table.yaml.

Rules:
  - If task_type is not in the table → returns the UNCLASSIFIED sentinel
  - Unclassified tasks ESCALATE — they are NEVER executed
  - This is a safety property: unknown task types cannot run by default

The task type controls:
  - permission_set (what the agent may do)
  - permission_ceiling (the maximum allowed; role cards may be more restrictive)
  - required_reads (what must be resolved before execution)
  - writeback_expectations (what outputs are expected)
  - escalation_trigger (conditions that force escalation regardless of manifest)

Public API:
    classify(task_type_id, vault_root=None) -> dict
    list_task_types(vault_root=None) -> list[dict]

The unclassified sentinel is always available as UNCLASSIFIED_SENTINEL.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Any

try:
    import yaml  # type: ignore
except Exception:
    yaml = None


# ── Unclassified sentinel ─────────────────────────────────────────────────────
# Returned when a task_type_id has no match in the table.
# This ensures unknown task types never run silently.

UNCLASSIFIED_SENTINEL: dict = {
    "id": "unclassified",
    "description": "SENTINEL — task type could not be classified; escalate, never run",
    "required_reads": [],
    "optional_reads": [],
    "runtime_class": "escalate",
    "permission_set": [],
    "permission_ceiling": "none",
    "writeback_expectations": "escalation log entry only",
    "escalation_trigger": ["always — unclassified tasks are NEVER executed"],
}


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

    def _parse_string_list(lines: list[str], start: int, indent: int) -> tuple[list[Any], int]:
        values: list[Any] = []
        i = start
        while i < len(lines):
            i = _next_content_index(lines, i)
            if i >= len(lines):
                break
            raw = lines[i].rstrip()
            if _indent(raw) != indent or not raw.strip().startswith("- "):
                break
            values.append(_coerce_scalar(raw.strip()[2:].strip()))
            i += 1
        return values, i

    result: dict[str, Any] = {}
    lines = text.splitlines()
    i = 0
    saw_content = False
    while i < len(lines):
        i = _next_content_index(lines, i)
        if i >= len(lines):
            break
        raw = lines[i].rstrip()
        stripped = raw.strip()
        saw_content = True
        if raw.startswith(" ") or ":" not in stripped:
            raise ValueError(f"Unsupported YAML syntax on line {i + 1}: {raw}")
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise ValueError(f"Empty mapping key on line {i + 1}")
        if value != "":
            result[key] = _coerce_scalar(value)
            i += 1
            continue

        if key != "task_types":
            raise ValueError(f"Unsupported nested mapping '{key}' in task type table fallback parser")

        items: list[dict[str, Any]] = []
        i += 1
        while i < len(lines):
            i = _next_content_index(lines, i)
            if i >= len(lines):
                break
            raw_item = lines[i].rstrip()
            if _indent(raw_item) < 2:
                break
            if _indent(raw_item) != 2 or not raw_item.strip().startswith("- "):
                raise ValueError(f"Unsupported YAML syntax on line {i + 1}: {raw_item}")

            item_value = raw_item.strip()[2:].strip()
            if ":" not in item_value:
                raise ValueError(f"Expected key:value list item on line {i + 1}: {raw_item}")
            first_key, first_val = item_value.split(":", 1)
            first_key = first_key.strip()
            first_val = first_val.strip()
            if not first_key:
                raise ValueError(f"Empty list-item key on line {i + 1}")

            entry: dict[str, Any] = {first_key: _coerce_scalar(first_val) if first_val else ""}
            i += 1

            while i < len(lines):
                i = _next_content_index(lines, i)
                if i >= len(lines):
                    break
                raw_child = lines[i].rstrip()
                child_indent = _indent(raw_child)
                child_stripped = raw_child.strip()
                if child_indent < 4:
                    break
                if child_indent != 4 or ":" not in child_stripped:
                    raise ValueError(f"Unsupported YAML syntax on line {i + 1}: {raw_child}")

                subkey, subvalue = child_stripped.split(":", 1)
                subkey = subkey.strip()
                subvalue = subvalue.strip()
                if not subkey:
                    raise ValueError(f"Empty mapping key on line {i + 1}")

                if subvalue:
                    entry[subkey] = _coerce_scalar(subvalue)
                    i += 1
                    continue

                values, next_i = _parse_string_list(lines, i + 1, 6)
                entry[subkey] = values
                i = next_i

            items.append(entry)

        result[key] = items

    if not saw_content:
        raise ValueError("Task type table is empty")
    return result


# ── Table location ────────────────────────────────────────────────────────────

def _get_table_path(vault_root: Optional[Path] = None) -> Path:
    """
    The task_type_table.yaml lives alongside this module in runtime/aor/.
    When a vault_root is provided, prefer the vault-owned table. This matters
    for packaged runtimes, where __file__ points at the extracted application
    bundle instead of the operator's ChaseOS workspace.
    """
    if vault_root is not None:
        candidate = Path(vault_root).resolve() / "runtime" / "aor" / "task_type_table.yaml"
        if candidate.exists():
            return candidate
    here = Path(__file__).resolve()
    return here.parent / "task_type_table.yaml"


def _detect_vault_root() -> Path:
    here = Path(__file__).resolve()
    vault_root = here.parents[2]
    if not (vault_root / "CLAUDE.md").exists():
        raise RuntimeError(
            f"Could not detect vault root. Expected CLAUDE.md at: {vault_root}\n"
            "Use vault_root parameter to specify the vault path explicitly."
        )
    return vault_root


# ── Table loading ─────────────────────────────────────────────────────────────

def _load_table(vault_root: Optional[Path] = None) -> dict[str, dict]:
    """
    Load task_type_table.yaml and return a dict keyed by task type id.
    The unclassified sentinel is always present regardless of table contents.
    """
    table_path = _get_table_path(vault_root)

    if not table_path.exists():
        return {"unclassified": UNCLASSIFIED_SENTINEL}

    with table_path.open("r", encoding="utf-8") as f:
        text = f.read()
    if yaml is not None:
        raw = yaml.safe_load(text)
    else:
        raw = _parse_simple_yaml(text)

    if not isinstance(raw, dict) or "task_types" not in raw:
        return {"unclassified": UNCLASSIFIED_SENTINEL}

    table: dict[str, dict] = {}
    for entry in raw["task_types"]:
        if isinstance(entry, dict) and "id" in entry:
            table[entry["id"]] = entry

    # Always ensure sentinel is present (overrides any table entry with that id)
    table["unclassified"] = UNCLASSIFIED_SENTINEL
    return table


# ── Public API ────────────────────────────────────────────────────────────────

def classify(
    task_type_id: str,
    vault_root: Optional[Path] = None,
) -> dict:
    """
    Classify a task_type_id, returning the full TaskType definition.

    If the task_type_id is not found in the table, returns UNCLASSIFIED_SENTINEL.
    An unclassified result means the workflow MUST escalate — never run.
    """
    table = _load_table(vault_root)
    return table.get(task_type_id, UNCLASSIFIED_SENTINEL)


def list_task_types(vault_root: Optional[Path] = None) -> list[dict]:
    """
    Return all task type definitions from the table, excluding the sentinel.
    Useful for introspection and validation tooling.
    """
    table = _load_table(vault_root)
    return [v for k, v in table.items() if k != "unclassified"]
