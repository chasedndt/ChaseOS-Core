"""Typed contracts for the ChaseOS Connections layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

PermissionProfile = Literal[
    "read_only",
    "assist",
    "action_confirmed",
    "action_allowlisted",
    "autonomous_project",
]

ActionType = Literal["read", "draft", "write", "delete", "external_egress"]
SafetyLevel = Literal["safe", "approval_required", "restricted", "disabled"]
ConnectionStatus = Literal["connected", "disconnected", "error", "expired", "pending_admin"]
AuthMethod = Literal["oauth", "api_key", "local_bridge", "remote_mcp", "none"]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class Capability:
    tool_name: str
    action_type: ActionType
    safety_level: SafetyLevel = "safe"
    enabled_by_default: bool = False
    description: str = ""
    constraints: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ConnectionManifest:
    id: str
    name: str
    category: str
    version: str
    status: str
    supports: dict[str, bool]
    auth: dict[str, Any]
    permission_profiles: dict[str, Any]
    capabilities: list[Capability]
    safety: dict[str, Any]
    connection_modes: list[str] = field(default_factory=list)
    ingress: dict[str, Any] = field(default_factory=dict)
    egress: dict[str, Any] = field(default_factory=dict)
    memory: dict[str, Any] = field(default_factory=dict)
    deployment: dict[str, Any] = field(default_factory=dict)
    sync: dict[str, Any] = field(default_factory=dict)
    normalization: dict[str, Any] = field(default_factory=dict)
    source_path: str = ""

    def default_profile(self) -> str:
        return str(self.safety.get("default_profile") or "read_only")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "version": self.version,
            "status": self.status,
            "supports": self.supports,
            "auth": self.auth,
            "permission_profiles": self.permission_profiles,
            "capabilities": [cap.__dict__ for cap in self.capabilities],
            "safety": self.safety,
            "connection_modes": self.connection_modes,
            "ingress": self.ingress,
            "egress": self.egress,
            "memory": self.memory,
            "deployment": self.deployment,
            "sync": self.sync,
            "normalization": self.normalization,
            "source_path": self.source_path,
        }
