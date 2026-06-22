"""runtime.config.store — bounded Phase 9 operator config surface.

Non-secret operator/runtime-shell preferences only.
Config remains subordinate to Gate, manifests, and policy binding.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

DEFAULT_CONFIG: dict[str, Any] = {
    "default_provider": None,
    "default_runtime": None,
    "log_verbosity": "normal",
    "scaffold_profile": "default",
    "scaffold_defaults": {
        "project_root": None,
        "workspace_root": None,
    },
}

ALLOWED_TOP_LEVEL_KEYS: set[str] = {
    "default_provider",
    "default_runtime",
    "log_verbosity",
    "scaffold_profile",
    "scaffold_defaults",
}

ALLOWED_NESTED_KEYS: dict[str, set[str]] = {
    "scaffold_defaults": {"project_root", "workspace_root"},
}

SECRET_KEY_PATTERN = re.compile(r"(api[_-]?key|secret|token|password|credential|connection[_-]?string)", re.IGNORECASE)
ALLOWED_LOG_VERBOSITY = {"quiet", "normal", "verbose", "debug"}
ALLOWED_SCAFFOLD_PROFILES = {"default"}


def _issue(code: str, path: str, message: str, severity: str = "error") -> dict[str, str]:
    return {
        "code": code,
        "severity": severity,
        "path": path,
        "message": message,
    }


def _is_secret_like_key(key: str) -> bool:
    return bool(SECRET_KEY_PATTERN.search(key))


def _validate_path_value(value: Any, path: str) -> list[dict[str, str]]:
    if value is None:
        return []
    if not isinstance(value, str):
        return [_issue("invalid_type", path, "Path-like config values must be strings or null")]
    if value.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", value):
        return [_issue("absolute_path_not_allowed", path, "Config paths must stay vault-relative")]
    parts = value.replace("\\", "/").split("/")
    if ".." in parts:
        return [_issue("parent_traversal_not_allowed", path, "Config paths must not contain '..'")]
    return []


def validate_config_payload(payload: dict[str, Any], *, config_path: Optional[Path] = None) -> dict[str, Any]:
    """Validate bounded non-secret config without mutating it."""
    issues: list[dict[str, str]] = []

    for key, value in payload.items():
        key_path = key
        if _is_secret_like_key(key):
            issues.append(_issue("secret_like_key", key_path, "Secrets do not belong in .chaseos/config.yaml"))
        if key not in ALLOWED_TOP_LEVEL_KEYS:
            issues.append(_issue("unknown_top_level_key", key_path, "Unknown top-level config key"))
            if isinstance(value, dict):
                for nested_key in value:
                    nested_path = f"{key}.{nested_key}"
                    if _is_secret_like_key(nested_key):
                        issues.append(_issue("secret_like_key", nested_path, "Secrets do not belong in .chaseos/config.yaml"))
            continue
        if isinstance(value, dict):
            allowed_nested = ALLOWED_NESTED_KEYS.get(key, set())
            for nested_key, nested_value in value.items():
                nested_path = f"{key}.{nested_key}"
                if _is_secret_like_key(nested_key):
                    issues.append(_issue("secret_like_key", nested_path, "Secrets do not belong in .chaseos/config.yaml"))
                if nested_key not in allowed_nested:
                    issues.append(_issue("unknown_nested_key", nested_path, "Unknown nested config key"))
                    continue
                if key == "scaffold_defaults":
                    issues.extend(_validate_path_value(nested_value, nested_path))

    log_verbosity = payload.get("log_verbosity")
    if log_verbosity not in ALLOWED_LOG_VERBOSITY:
        issues.append(
            _issue(
                "invalid_value",
                "log_verbosity",
                f"log_verbosity must be one of: {', '.join(sorted(ALLOWED_LOG_VERBOSITY))}",
            )
        )

    scaffold_profile = payload.get("scaffold_profile")
    if scaffold_profile not in ALLOWED_SCAFFOLD_PROFILES:
        issues.append(
            _issue(
                "invalid_value",
                "scaffold_profile",
                f"scaffold_profile must be one of: {', '.join(sorted(ALLOWED_SCAFFOLD_PROFILES))}",
            )
        )

    for key in ("default_provider", "default_runtime"):
        value = payload.get(key)
        if value is not None and not isinstance(value, str):
            issues.append(_issue("invalid_type", key, f"{key} must be a string or null"))

    ok = not any(issue["severity"] == "error" for issue in issues)
    return {
        "ok": ok,
        "posture": "ready" if ok else "blocked",
        "read_only": True,
        "mutates_config": False,
        "config_path": str(config_path) if config_path else None,
        "schema": {
            "allowed_top_level_keys": sorted(ALLOWED_TOP_LEVEL_KEYS),
            "allowed_nested_keys": {key: sorted(value) for key, value in sorted(ALLOWED_NESTED_KEYS.items())},
            "allowed_log_verbosity": sorted(ALLOWED_LOG_VERBOSITY),
            "secret_key_pattern": SECRET_KEY_PATTERN.pattern,
        },
        "issues": issues,
    }


def validate_config_store(*, vault_root: Optional[Path] = None) -> dict[str, Any]:
    path = ensure_config_store(vault_root=vault_root)
    payload = _parse_simple_yaml(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return {
            "ok": False,
            "posture": "blocked",
            "read_only": True,
            "mutates_config": False,
            "config_path": str(path),
            "schema": {
                "allowed_top_level_keys": sorted(ALLOWED_TOP_LEVEL_KEYS),
                "allowed_nested_keys": {key: sorted(value) for key, value in sorted(ALLOWED_NESTED_KEYS.items())},
                "allowed_log_verbosity": sorted(ALLOWED_LOG_VERBOSITY),
                "secret_key_pattern": SECRET_KEY_PATTERN.pattern,
            },
            "issues": [_issue("invalid_root_type", "", "Config store must parse to a mapping")],
        }
    return validate_config_payload(payload, config_path=path)


def _detect_vault_root() -> Path:
    current = Path(__file__).resolve()
    for candidate in [current] + list(current.parents):
        if (candidate / "CLAUDE.md").exists():
            return candidate
    raise FileNotFoundError("Could not locate ChaseOS vault root (CLAUDE.md not found)")



def _config_path(vault_root: Optional[Path] = None) -> Path:
    root = Path(vault_root) if vault_root else _detect_vault_root()
    return root / ".chaseos" / "config.yaml"



def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)



def _coerce_scalar(value: str) -> Any:
    value = value.strip()
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



def _emit_yaml_lines(value: Any, indent: int = 0) -> list[str]:
    prefix = " " * indent
    if isinstance(value, dict):
        lines: list[str] = []
        for key, child in value.items():
            if isinstance(child, dict):
                lines.append(f"{prefix}{key}:")
                lines.extend(_emit_yaml_lines(child, indent + 2))
            else:
                lines.append(f"{prefix}{key}: {_yaml_scalar(child)}")
        return lines
    return [f"{prefix}{_yaml_scalar(value)}"]



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
        return {}

    def parse_mapping(index: int, indent: int) -> tuple[dict[str, Any], int]:
        result: dict[str, Any] = {}
        while index < len(parsed_lines):
            current_indent, current_text, line_no = parsed_lines[index]
            if current_indent < indent:
                break
            if current_indent != indent:
                raise ValueError(f"Unsupported YAML indentation on line {line_no}: {current_text}")
            if ":" not in current_text:
                raise ValueError(f"Unsupported YAML syntax on line {line_no}: {current_text}")
            key, raw_value = current_text.split(":", 1)
            key = key.strip()
            value = raw_value.strip()
            index += 1
            if value:
                result[key] = _coerce_scalar(value)
                continue
            if index >= len(parsed_lines) or parsed_lines[index][0] <= current_indent:
                result[key] = {}
                continue
            nested, index = parse_mapping(index, parsed_lines[index][0])
            result[key] = nested
        return result, index

    result, _ = parse_mapping(0, parsed_lines[0][0])
    return result



def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = json.loads(json.dumps(base))
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged



def ensure_config_store(*, vault_root: Optional[Path] = None) -> Path:
    path = _config_path(vault_root)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(_emit_yaml_lines(DEFAULT_CONFIG)) + "\n", encoding="utf-8")
    return path



def load_config_store(*, vault_root: Optional[Path] = None) -> dict[str, Any]:
    path = ensure_config_store(vault_root=vault_root)
    payload = _parse_simple_yaml(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Config store must parse to a mapping")
    merged = _deep_merge(DEFAULT_CONFIG, payload)
    return merged



def write_config_store(state: dict[str, Any], *, vault_root: Optional[Path] = None) -> Path:
    path = _config_path(vault_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(_emit_yaml_lines(state)) + "\n", encoding="utf-8")
    return path



def set_config_value(key: str, value: Any, *, vault_root: Optional[Path] = None) -> tuple[dict[str, Any], Path]:
    state = load_config_store(vault_root=vault_root)
    parts = key.split(".")
    top_level = parts[0]
    if top_level not in ALLOWED_TOP_LEVEL_KEYS:
        raise ValueError(f"Unknown config key: {key}")
    if len(parts) == 1:
        if isinstance(state.get(top_level), dict):
            raise ValueError(f"Config key requires nested field: {key}")
        state[top_level] = value
    elif len(parts) == 2:
        nested_parent, nested_key = parts
        allowed_nested = ALLOWED_NESTED_KEYS.get(nested_parent, set())
        if nested_key not in allowed_nested:
            raise ValueError(f"Unknown config key: {key}")
        nested = state.setdefault(nested_parent, {})
        if not isinstance(nested, dict):
            raise ValueError(f"Config parent is not a mapping: {nested_parent}")
        nested[nested_key] = value
    else:
        raise ValueError(f"Unsupported config key depth: {key}")
    path = write_config_store(state, vault_root=vault_root)
    return state, path
