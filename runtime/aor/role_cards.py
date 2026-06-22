"""
role_cards.py — ChaseOS AOR Phase 9

Agent Role Card loader and validator.

Role cards are runtime boundary files — NOT personas.
They define the exact permission envelope for a workflow execution.
Role cards live at: 06_AGENTS/role-cards/

Each role card is a YAML file named <card_id>.yaml.
Files beginning with "_" are schema/meta files and are skipped.

Role cards are the authoritative source for:
  - allowed_actions: what the agent may do during this workflow
  - forbidden_actions: hard stops that force escalation
  - write_scope: which directories/files the agent may write
  - forbidden_write_zones: directories/files that can NEVER be written

Public API:
    load_card(card_id, vault_root=None) -> dict | None
    list_cards(vault_root=None) -> list[dict]
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Any

from .path_policy import AORPathPolicyError, validate_vault_relative_path_list

try:
    import yaml  # type: ignore
except Exception:
    yaml = None


# ── Required fields for a valid role card ─────────────────────────────────────

CARD_REQUIRED_FIELDS: list[str] = [
    "id",
    "name",
    "version",
    "description",
    "owner",
    "allowed_actions",
    "forbidden_actions",
    "write_scope",
    "forbidden_write_zones",
    "escalation_rules",
    "runtime_expectations",
]


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
    result: dict[str, Any] = {}
    lines = text.splitlines()
    i = 0
    saw_content = False
    while i < len(lines):
        raw = lines[i].rstrip()
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or stripped == "---":
            i += 1
            continue
        saw_content = True
        if raw.startswith(" ") or ":" not in stripped:
            raise ValueError(f"Unsupported YAML syntax on line {i + 1}: {raw}")
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise ValueError(f"Empty mapping key on line {i + 1}")
        if value in {">", "|"}:
            block_lines: list[str] = []
            i += 1
            while i < len(lines):
                child = lines[i].rstrip("\n")
                if not child.strip():
                    block_lines.append("")
                    i += 1
                    continue
                if not child.startswith("  "):
                    break
                block_lines.append(child[2:])
                i += 1
            result[key] = "\n".join(block_lines).strip()
            continue
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
                        raise ValueError(f"Empty list item on line {i + 1}")
                    if ":" in item_value:
                        item_dict: dict[str, Any] = {}
                        item_key, item_val = item_value.split(":", 1)
                        item_key = item_key.strip()
                        if not item_key:
                            raise ValueError(f"Empty list-item key on line {i + 1}")
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
                                raise ValueError(f"Unsupported YAML syntax on line {i + 1}: {grandchild}")
                            subkey, subvalue = grandchild_stripped.split(":", 1)
                            subkey = subkey.strip()
                            if not subkey:
                                raise ValueError(f"Empty mapping key on line {i + 1}")
                            item_dict[subkey] = _coerce_scalar(subvalue.strip())
                            i += 1
                        items.append(item_dict)
                        continue
                    items.append(_coerce_scalar(item_value))
                    i += 1
                    continue
                if ":" not in child_stripped:
                    raise ValueError(f"Unsupported YAML syntax on line {i + 1}: {child}")
                subkey, subvalue = child_stripped.split(":", 1)
                subkey = subkey.strip()
                if not subkey:
                    raise ValueError(f"Empty mapping key on line {i + 1}")
                nested[subkey] = _coerce_scalar(subvalue.strip())
                i += 1
            if not saw_child:
                raise ValueError(f"Expected indented block after '{key}:'")
            if items and nested:
                raise ValueError(f"Mixed list/mapping block not supported for '{key}'")
            result[key] = items if items else nested
            continue
        result[key] = _coerce_scalar(value)
        i += 1
    if not saw_content:
        raise ValueError("Role card is empty")
    return result


# ── Vault root detection ───────────────────────────────────────────────────────

def _detect_vault_root() -> Path:
    here = Path(__file__).resolve()
    vault_root = here.parents[2]  # runtime/aor/role_cards.py → vault root
    if not (vault_root / "CLAUDE.md").exists():
        raise RuntimeError(
            f"Could not detect vault root. Expected CLAUDE.md at: {vault_root}\n"
            "Use vault_root parameter to specify the vault path explicitly."
        )
    return vault_root


def _get_cards_dir(vault_root: Path) -> Path:
    return vault_root / "06_AGENTS" / "role-cards"


# ── Validation ────────────────────────────────────────────────────────────────

def _validate_card(data: dict, path: Path) -> None:
    """
    Validate a role card dict has all required fields.
    Raises ValueError with a descriptive message if invalid.
    """
    missing = [f for f in CARD_REQUIRED_FIELDS if f not in data]
    if missing:
        raise ValueError(
            f"Role card at {path} is missing required fields: {missing}"
        )

    # id must match the filename stem
    expected_id = path.stem
    if data.get("id") != expected_id:
        raise ValueError(
            f"Role card at {path}: id field ({data.get('id')!r}) "
            f"must match filename stem ({expected_id!r})"
        )

    for list_field in ("allowed_actions", "forbidden_actions", "write_scope",
                       "forbidden_write_zones", "escalation_rules", "runtime_expectations"):
        if not isinstance(data.get(list_field), list):
            raise ValueError(
                f"Role card at {path}: {list_field!r} must be a list"
            )

    try:
        for path_field in ("write_scope", "forbidden_write_zones", "required_reads", "optional_reads"):
            if path_field in data:
                validate_vault_relative_path_list(
                    data[path_field],
                    f"Role card at {path}: {path_field}",
                    skip_runtime_placeholders=True,
                )
    except AORPathPolicyError as exc:
        raise ValueError(str(exc)) from exc


# ── Public API ────────────────────────────────────────────────────────────────

def load_card(
    card_id: str,
    vault_root: Optional[Path] = None,
) -> Optional[dict]:
    """
    Load and validate a role card by ID.

    Returns the role card dict on success.
    Returns None if the role card file does not exist.
    Raises ValueError if the card exists but fails validation.
    """
    if vault_root is None:
        vault_root = _detect_vault_root()

    cards_dir = _get_cards_dir(vault_root)
    card_path = cards_dir / f"{card_id}.yaml"

    if not card_path.exists():
        return None

    with card_path.open("r", encoding="utf-8") as f:
        text = f.read()
    if yaml is not None:
        data = yaml.safe_load(text)
    else:
        data = _parse_simple_yaml(text)

    if not isinstance(data, dict):
        raise ValueError(
            f"Role card at {card_path} did not parse as a YAML mapping"
        )

    _validate_card(data, card_path)
    return data


def list_cards(vault_root: Optional[Path] = None) -> list[dict]:
    """
    Load and return all valid role cards from the role-cards directory.

    Files beginning with "_" are skipped (schema/meta files).
    Cards that fail validation are skipped with a warning logged to stderr.
    """
    import sys

    if vault_root is None:
        vault_root = _detect_vault_root()

    cards_dir = _get_cards_dir(vault_root)

    if not cards_dir.exists():
        return []

    results: list[dict] = []
    for path in sorted(cards_dir.glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        try:
            card = load_card(path.stem, vault_root)
            if card is not None:
                results.append(card)
        except Exception as exc:
            print(f"AOR role-cards: skipping {path.name} — {exc}", file=sys.stderr)

    return results
