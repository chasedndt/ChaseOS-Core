"""
Runtime model configuration loader.

Each runtime (openclaw, hermes) declares its primary model and fallback chain
in runtime/{runtime}/model_config.yaml. This module loads and validates that config.

No anthropic SDK — API calls are stdlib urllib.request in execute.py.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Any

try:
    import yaml  # type: ignore
except Exception:
    yaml = None


class ModelConfigError(Exception):
    """Raised when a runtime model_config.yaml is missing or malformed."""


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
            raise ModelConfigError(f"Unsupported YAML syntax on line {i + 1}: {raw}")
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise ModelConfigError(f"Empty mapping key on line {i + 1}")
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
                        raise ModelConfigError(f"Empty list item on line {i + 1}")
                    if ":" in item_value:
                        item_dict: dict[str, Any] = {}
                        item_key, item_val = item_value.split(":", 1)
                        item_key = item_key.strip()
                        if not item_key:
                            raise ModelConfigError(f"Empty list-item key on line {i + 1}")
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
                                raise ModelConfigError(f"Unsupported YAML syntax on line {i + 1}: {grandchild}")
                            subkey, subvalue = grandchild_stripped.split(":", 1)
                            subkey = subkey.strip()
                            if not subkey:
                                raise ModelConfigError(f"Empty mapping key on line {i + 1}")
                            item_dict[subkey] = _coerce_scalar(subvalue.strip())
                            i += 1
                        items.append(item_dict)
                        continue
                    items.append(_coerce_scalar(item_value))
                    i += 1
                    continue
                if ":" not in child_stripped:
                    raise ModelConfigError(f"Unsupported YAML syntax on line {i + 1}: {child}")
                subkey, subvalue = child_stripped.split(":", 1)
                subkey = subkey.strip()
                if not subkey:
                    raise ModelConfigError(f"Empty mapping key on line {i + 1}")
                nested[subkey] = _coerce_scalar(subvalue.strip())
                i += 1
            if not saw_child:
                raise ModelConfigError(f"Expected indented block after '{key}:'")
            if items and nested:
                raise ModelConfigError(f"Mixed list/mapping block not supported for '{key}'")
            result[key] = items if items else nested
            continue
        result[key] = _coerce_scalar(value)
        i += 1
    if not saw_content:
        raise ModelConfigError("Model config manifest is empty")
    return result


@dataclass(frozen=True)
class ModelSpec:
    model_id: str
    max_tokens: int = 4096
    temperature: float = 0.3

    def __post_init__(self) -> None:
        if not self.model_id or not isinstance(self.model_id, str):
            raise ModelConfigError("model_id must be a non-empty string")
        if self.max_tokens < 1:
            raise ModelConfigError("max_tokens must be >= 1")
        if not (0.0 <= self.temperature <= 2.0):
            raise ModelConfigError("temperature must be between 0.0 and 2.0")


@dataclass
class RuntimeModelConfig:
    runtime_name: str
    primary: ModelSpec
    fallbacks: list[ModelSpec] = field(default_factory=list)

    def all_models(self) -> Iterator[ModelSpec]:
        """Yields primary then each fallback in order."""
        yield self.primary
        yield from self.fallbacks


def load_runtime_model_config(runtime_name: str, vault_root: str | Path) -> RuntimeModelConfig:
    """
    Load model_config.yaml for the given runtime.

    Expected location: runtime/{runtime_name}/model_config.yaml
    Raises ModelConfigError if file is missing or malformed.
    """
    config_path = Path(vault_root) / "runtime" / runtime_name / "model_config.yaml"
    if not config_path.exists():
        raise ModelConfigError(
            f"No model_config.yaml found for runtime '{runtime_name}' at {config_path}"
        )

    try:
        text = config_path.read_text(encoding="utf-8")
        if yaml is not None:
            raw = yaml.safe_load(text)
        else:
            raw = _parse_simple_yaml(text)
    except Exception as exc:
        raise ModelConfigError(f"Failed to parse model_config.yaml for '{runtime_name}': {exc}") from exc

    if not isinstance(raw, dict):
        raise ModelConfigError(f"model_config.yaml for '{runtime_name}' must be a YAML mapping")

    primary_raw = raw.get("primary")
    if not primary_raw:
        raise ModelConfigError(f"model_config.yaml for '{runtime_name}' missing required 'primary' field")

    primary = _parse_model_spec(primary_raw, context=f"{runtime_name}.primary")

    fallbacks: list[ModelSpec] = []
    for i, fb_raw in enumerate(raw.get("fallbacks", []) or []):
        fallbacks.append(_parse_model_spec(fb_raw, context=f"{runtime_name}.fallbacks[{i}]"))

    return RuntimeModelConfig(
        runtime_name=runtime_name,
        primary=primary,
        fallbacks=fallbacks,
    )


def _parse_model_spec(raw: object, context: str) -> ModelSpec:
    if isinstance(raw, str):
        return ModelSpec(model_id=raw)
    if not isinstance(raw, dict):
        raise ModelConfigError(f"{context}: model spec must be a string or mapping, got {type(raw).__name__}")
    model_id = raw.get("model_id") or raw.get("model")
    if not model_id:
        raise ModelConfigError(f"{context}: model spec missing 'model_id' field")
    kwargs: dict = {"model_id": model_id}
    if "max_tokens" in raw:
        kwargs["max_tokens"] = int(raw["max_tokens"])
    if "temperature" in raw:
        kwargs["temperature"] = float(raw["temperature"])
    return ModelSpec(**kwargs)
