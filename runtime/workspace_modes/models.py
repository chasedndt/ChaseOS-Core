"""Workspace Mode Layer profile model and validation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


ALLOWED_WORKSPACE_MODES: tuple[str, ...] = (
    "personal_os",
    "study_research",
    "founder_venture",
    "business_ops",
    "runtime_agent_ops",
    "unknown",
)

ALLOWED_KNOWLEDGE_CLASSES: tuple[str, ...] = (
    "user-origin",
    "source-derived",
    "synthesized",
    "generated-ideas",
    "system-operational",
    "canonical-state",
)

ALLOWED_ADAPTER_CEILING_VALUES: tuple[str, ...] = (
    "tier-2",
    "tier-2-bounded",
    "tier-3",
    "tier-4",
    "tier-4-default-tier-2-bounded",
    "blocked",
)

REQUIRED_PROFILE_FIELDS: tuple[str, ...] = (
    "workspace_id",
    "workspace_name",
    "workspace_mode",
    "description",
    "primary_domains",
    "canonical_state_files",
    "required_read_order",
    "allowed_knowledge_classes",
    "default_output_classes",
    "allowed_workflows",
    "runtime_adapter_ceiling",
    "approval_rules",
    "graph_rules",
    "protected_paths",
    "default_write_targets",
    "escalation_rules",
)

LIST_FIELDS: tuple[str, ...] = (
    "primary_domains",
    "canonical_state_files",
    "required_read_order",
    "allowed_knowledge_classes",
    "default_output_classes",
    "allowed_workflows",
    "protected_paths",
    "default_write_targets",
)

OPTIONAL_LIST_FIELDS: tuple[str, ...] = (
    "allowed_workspace_roots",
    "allowed_external_workspace_roots",
)

MAPPING_FIELDS: tuple[str, ...] = (
    "runtime_adapter_ceiling",
    "approval_rules",
    "graph_rules",
    "escalation_rules",
)


class WorkspaceModeValidationError(ValueError):
    """Raised when a workspace mode profile fails validation."""


@dataclass(frozen=True)
class WorkspaceModeProfile:
    workspace_id: str
    workspace_name: str
    workspace_mode: str
    description: str
    primary_domains: tuple[str, ...]
    canonical_state_files: tuple[str, ...]
    required_read_order: tuple[str, ...]
    allowed_knowledge_classes: tuple[str, ...]
    default_output_classes: tuple[str, ...]
    allowed_workflows: tuple[str, ...]
    runtime_adapter_ceiling: dict[str, str]
    approval_rules: dict[str, Any]
    graph_rules: dict[str, Any]
    protected_paths: tuple[str, ...]
    default_write_targets: tuple[str, ...]
    escalation_rules: dict[str, Any]
    allowed_workspace_roots: tuple[str, ...] = ()
    allowed_external_workspace_roots: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "WorkspaceModeProfile":
        validate_profile_mapping(data)
        return cls(
            workspace_id=str(data["workspace_id"]),
            workspace_name=str(data["workspace_name"]),
            workspace_mode=str(data["workspace_mode"]),
            description=str(data["description"]),
            primary_domains=tuple(str(value) for value in data["primary_domains"]),
            canonical_state_files=tuple(str(value) for value in data["canonical_state_files"]),
            required_read_order=tuple(str(value) for value in data["required_read_order"]),
            allowed_knowledge_classes=tuple(str(value) for value in data["allowed_knowledge_classes"]),
            default_output_classes=tuple(str(value) for value in data["default_output_classes"]),
            allowed_workflows=tuple(str(value) for value in data["allowed_workflows"]),
            runtime_adapter_ceiling=dict(data["runtime_adapter_ceiling"]),
            approval_rules=dict(data["approval_rules"]),
            graph_rules=dict(data["graph_rules"]),
            protected_paths=tuple(str(value) for value in data["protected_paths"]),
            default_write_targets=tuple(str(value) for value in data["default_write_targets"]),
            escalation_rules=dict(data["escalation_rules"]),
            allowed_workspace_roots=tuple(
                str(value) for value in data.get("allowed_workspace_roots", [])
            ),
            allowed_external_workspace_roots=tuple(
                str(value) for value in data.get("allowed_external_workspace_roots", [])
            ),
        )

    @property
    def is_unknown(self) -> bool:
        return self.workspace_mode == "unknown"

    @property
    def requires_strict_runtime_controls(self) -> bool:
        return self.workspace_mode in {"runtime_agent_ops", "unknown"}

    @property
    def canonical_writes_require_approval(self) -> bool:
        rule = str(self.approval_rules.get("canonical_state_write", "")).lower()
        return "approval" in rule or self.requires_strict_runtime_controls

    def adapter_ceiling_for(self, adapter: str) -> str:
        return str(self.runtime_adapter_ceiling.get(adapter, "blocked"))


def validate_profile_mapping(data: Mapping[str, Any]) -> None:
    missing = [field for field in REQUIRED_PROFILE_FIELDS if field not in data]
    if missing:
        raise WorkspaceModeValidationError(f"workspace profile missing required fields: {missing}")

    workspace_mode = str(data["workspace_mode"])
    if workspace_mode not in ALLOWED_WORKSPACE_MODES:
        raise WorkspaceModeValidationError(f"invalid workspace_mode: {workspace_mode!r}")

    for field in LIST_FIELDS:
        if not isinstance(data[field], list):
            raise WorkspaceModeValidationError(f"{field} must be a list")

    for field in OPTIONAL_LIST_FIELDS:
        if field in data and not isinstance(data[field], list):
            raise WorkspaceModeValidationError(f"{field} must be a list")

    for field in MAPPING_FIELDS:
        if not isinstance(data[field], dict):
            raise WorkspaceModeValidationError(f"{field} must be a mapping")

    invalid_classes = [
        value
        for value in data["allowed_knowledge_classes"]
        if str(value) not in ALLOWED_KNOWLEDGE_CLASSES
    ]
    if invalid_classes:
        raise WorkspaceModeValidationError(
            f"invalid allowed_knowledge_classes entries: {invalid_classes}"
        )

    invalid_ceilings = {
        adapter: value
        for adapter, value in data["runtime_adapter_ceiling"].items()
        if str(value) not in ALLOWED_ADAPTER_CEILING_VALUES
    }
    if invalid_ceilings:
        raise WorkspaceModeValidationError(
            f"invalid runtime_adapter_ceiling values: {invalid_ceilings}"
        )


def build_unknown_profile(context_path: str = "") -> WorkspaceModeProfile:
    """Return the fail-closed profile used when no mode can be resolved."""

    label = context_path or "unknown"
    return WorkspaceModeProfile(
        workspace_id="unknown",
        workspace_name=f"Unknown Workspace ({label})",
        workspace_mode="unknown",
        description="Fail-closed fallback profile for unresolved workspace context.",
        primary_domains=(),
        canonical_state_files=(),
        required_read_order=(),
        allowed_knowledge_classes=(),
        default_output_classes=("proposal", "log"),
        allowed_workflows=(),
        runtime_adapter_ceiling={
            "claude": "blocked",
            "codex": "blocked",
            "openclaw": "blocked",
            "hermes": "blocked",
        },
        approval_rules={
            "canonical_state_write": "blocked",
            "generated_idea_creation": "requires_explicit_approval",
            "generated_idea_endorsement": "human_only",
            "source_promotion": "gate_required",
            "protected_file_write": "blocked",
            "shell_execution": "blocked",
            "external_connector_action": "blocked",
        },
        graph_rules={
            "update_domain_index_on_promotion": False,
            "backlinks_required_for_durable_notes": True,
            "orphan_notes_flagged": True,
        },
        protected_paths=(),
        default_write_targets=(),
        escalation_rules={
            "unknown_mode": "stop_and_request_mode",
            "protected_write": "require_explicit_approval",
            "external_action": "require_explicit_approval",
            "runtime_authority_unclear": "fail_closed",
        },
        allowed_workspace_roots=(),
        allowed_external_workspace_roots=(),
    )
