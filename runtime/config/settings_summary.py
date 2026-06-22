"""Read-only settings/config summary for ChaseOS Runtime Shell.

This composes bounded operator config, provider setup state, and known runtime
identity into one Settings/Studio-ready payload. It does not mutate config or
grant authority; Gate, manifests, and role cards remain authoritative.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from runtime.config.store import DEFAULT_CONFIG, load_config_store, validate_config_payload, validate_config_store
from runtime.providers.registry import list_provider_status
from runtime.state.resolver import list_known_runtimes


def _detect_vault_root() -> Path:
    current = Path(__file__).resolve()
    for candidate in [current] + list(current.parents):
        if (candidate / "CLAUDE.md").exists():
            return candidate
    raise FileNotFoundError("Could not locate ChaseOS vault root (CLAUDE.md not found)")


def _copy_default_config() -> dict[str, Any]:
    return json.loads(json.dumps(DEFAULT_CONFIG))


def _attention(code: str, severity: str, message: str, *, target: str | None = None) -> dict[str, str]:
    item = {
        "code": code,
        "severity": severity,
        "message": message,
    }
    if target:
        item["target"] = target
    return item


def _provider_lookup(providers: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item.get("provider_id")): item for item in providers if item.get("provider_id")}


def _derive_posture(attention_items: list[dict[str, str]]) -> str:
    if any(item.get("severity") == "error" for item in attention_items):
        return "blocked"
    if any(item.get("severity") == "warning" for item in attention_items):
        return "degraded"
    return "ready"


def build_settings_summary(*, vault_root: Path | None = None) -> dict[str, Any]:
    """Build a read-only Settings/Studio-ready config summary."""
    root = Path(vault_root) if vault_root else _detect_vault_root()
    config_path = root / ".chaseos" / "config.yaml"
    config_present = config_path.exists()

    if config_present:
        config_values = load_config_store(vault_root=root)
        validation = validate_config_store(vault_root=root)
    else:
        config_values = _copy_default_config()
        validation = validate_config_payload(config_values, config_path=config_path)

    providers = list_provider_status()
    provider_by_id = _provider_lookup(providers)
    runtimes = list_known_runtimes()
    runtime_ids = {runtime.lower(): runtime for runtime in runtimes}

    attention_items: list[dict[str, str]] = []
    for issue in validation.get("issues") or []:
        severity = "error" if issue.get("severity") == "error" else "warning"
        attention_items.append(
            _attention(
                f"config_{issue.get('code')}",
                severity,
                str(issue.get("message") or "Config validation issue"),
                target=str(issue.get("path") or ""),
            )
        )

    default_provider = config_values.get("default_provider")
    default_runtime = config_values.get("default_runtime")

    if default_provider:
        provider = provider_by_id.get(str(default_provider))
        if provider is None:
            attention_items.append(
                _attention(
                    "unknown_default_provider",
                    "error",
                    "Configured default_provider is not present in the provider registry",
                    target=str(default_provider),
                )
            )
        elif not provider.get("valid"):
            attention_items.append(
                _attention(
                    "default_provider_not_valid",
                    "warning",
                    "Configured default_provider exists but is not currently valid",
                    target=str(default_provider),
                )
            )
    else:
        attention_items.append(
            _attention(
                "default_provider_not_set",
                "warning",
                "No operator default provider is configured; runtime/provider selection remains explicit or inferred",
            )
        )

    if default_runtime:
        runtime_key = str(default_runtime).lower()
        if runtime_key not in runtime_ids:
            attention_items.append(
                _attention(
                    "unknown_default_runtime",
                    "error",
                    "Configured default_runtime is not present in known lifecycle runtimes",
                    target=str(default_runtime),
                )
            )
    else:
        attention_items.append(
            _attention(
                "default_runtime_not_set",
                "warning",
                "No operator default runtime is configured; runtime selection remains explicit or inferred",
            )
        )

    valid_provider_count = sum(1 for provider in providers if provider.get("valid"))
    configured_provider_count = sum(1 for provider in providers if provider.get("configured"))
    if providers and valid_provider_count == 0:
        attention_items.append(
            _attention(
                "no_valid_providers",
                "warning",
                "No provider setup entry is currently valid",
            )
        )

    default_provider_status = provider_by_id.get(str(default_provider)) if default_provider else None
    default_runtime_known = runtime_ids.get(str(default_runtime).lower()) if default_runtime else None
    posture = _derive_posture(attention_items)

    next_actions: list[str] = []
    if not config_present:
        next_actions.append("run config list or config set to seed .chaseos/config.yaml if persistent defaults are desired")
    if any(item.get("code") == "default_provider_not_set" for item in attention_items):
        next_actions.append("set default_provider only if an operator default is desired")
    if any(item.get("code") == "default_runtime_not_set" for item in attention_items):
        next_actions.append("set default_runtime only if an operator default is desired")
    if any(item.get("severity") == "error" for item in attention_items):
        next_actions.append("fix blocking config validation/default identity errors before treating settings posture as ready")

    return {
        "action": "config-summary",
        "settings_posture": posture,
        "read_only": True,
        "mutates_config": False,
        "authority_expansion": False,
        "config": {
            "config_path": str(config_path),
            "config_present": config_present,
            "using_defaults": not config_present,
            "values": config_values,
            "validation": validation,
        },
        "provider_summary": {
            "known_count": len(providers),
            "configured_count": configured_provider_count,
            "valid_count": valid_provider_count,
            "default_provider": {
                "provider_id": default_provider,
                "known": default_provider_status is not None,
                "configured": default_provider_status.get("configured") if default_provider_status else None,
                "valid": default_provider_status.get("valid") if default_provider_status else None,
                "default_model": default_provider_status.get("default_model") if default_provider_status else None,
                "reasoning_policy": default_provider_status.get("reasoning_policy") if default_provider_status else None,
            },
            "providers": providers,
        },
        "runtime_summary": {
            "known_count": len(runtimes),
            "known_runtimes": runtimes,
            "default_runtime": {
                "runtime_id": default_runtime,
                "known": default_runtime_known is not None,
                "canonical_runtime_id": default_runtime_known,
            },
        },
        "governance": {
            "non_secret_config_only": True,
            "secrets_allowed_in_config": False,
            "config_grants_authority": False,
            "gate_overrides_config": True,
            "provider_switching_authority": False,
            "runtime_lifecycle_authority": False,
        },
        "attention_items": attention_items,
        "next_actions": next_actions,
    }
