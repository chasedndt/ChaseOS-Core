"""Provider manifest loading for ChaseOS Connections."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from runtime.connections.models import Capability, ConnectionManifest

DEFAULT_MANIFEST_DIR = Path(__file__).resolve().parent / "manifests"


class ManifestError(ValueError):
    """Raised when a connection manifest is missing required fields."""


def _as_bool_map(value: Any) -> dict[str, bool]:
    if not isinstance(value, dict):
        return {}
    return {str(k): bool(v) for k, v in value.items()}


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ManifestError(f"{path} must contain a YAML object")
    return data


def _capability_from_dict(raw: dict[str, Any]) -> Capability:
    if not isinstance(raw, dict):
        raise ManifestError("capability entries must be objects")
    tool_name = str(raw.get("tool_name") or "").strip()
    action_type = str(raw.get("action_type") or "").strip()
    if not tool_name or not action_type:
        raise ManifestError("capability requires tool_name and action_type")
    return Capability(
        tool_name=tool_name,
        action_type=action_type,  # type: ignore[arg-type]
        safety_level=str(raw.get("safety_level") or "safe"),  # type: ignore[arg-type]
        enabled_by_default=bool(raw.get("enabled_by_default", False)),
        description=str(raw.get("description") or ""),
        constraints=raw.get("constraints") if isinstance(raw.get("constraints"), dict) else {},
    )


def parse_manifest(data: dict[str, Any], *, source_path: str = "") -> ConnectionManifest:
    required = ["id", "name", "category", "version", "status"]
    missing = [key for key in required if not str(data.get(key) or "").strip()]
    if missing:
        raise ManifestError(f"manifest missing required fields: {missing}")
    capabilities = [_capability_from_dict(item) for item in data.get("capabilities", [])]
    permission_profiles = data.get("permissions", {}).get("profiles", {})
    if not isinstance(permission_profiles, dict):
        permission_profiles = {}
    return ConnectionManifest(
        id=str(data["id"]),
        name=str(data["name"]),
        category=str(data["category"]),
        version=str(data["version"]),
        status=str(data["status"]),
        supports=_as_bool_map(data.get("supports")),
        auth=_as_dict(data.get("auth")),
        permission_profiles=permission_profiles,
        capabilities=capabilities,
        safety=_as_dict(data.get("safety")),
        connection_modes=_as_str_list(data.get("connection_modes")),
        ingress=_as_dict(data.get("ingress")),
        egress=_as_dict(data.get("egress")),
        memory=_as_dict(data.get("memory")),
        deployment=_as_dict(data.get("deployment")),
        sync=_as_dict(data.get("sync")),
        normalization=_as_dict(data.get("normalization")),
        source_path=source_path,
    )


def load_manifest(provider_id: str, *, manifest_dir: Path = DEFAULT_MANIFEST_DIR) -> ConnectionManifest:
    safe_id = provider_id.replace("/", "").replace("\\", "").strip()
    path = manifest_dir / f"{safe_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"connection manifest not found: {path}")
    return parse_manifest(_load_yaml(path), source_path=str(path))


def list_provider_manifests(*, manifest_dir: Path = DEFAULT_MANIFEST_DIR) -> list[ConnectionManifest]:
    manifests: list[ConnectionManifest] = []
    for path in sorted(manifest_dir.glob("*.yaml")):
        manifests.append(parse_manifest(_load_yaml(path), source_path=str(path)))
    return manifests
