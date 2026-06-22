"""Models and validation for ChaseOS task-scoped sub-agent presets."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping


RUNTIME_BACKENDS: tuple[str, ...] = ("OpenHuman", "HermesAgent", "OpenClaw")
RUNTIME_BACKEND_TO_BUS_NAME: dict[str, str] = {
    "OpenHuman": "OpenHuman",
    "HermesAgent": "Hermes",
    "OpenClaw": "OpenClaw",
}
RETIRED_RUNTIME_BACKENDS: dict[str, str] = {
    "OpenHuman": "retired-reference-only in current repo truth",
}

CHASE_OS_MODES: tuple[str, ...] = (
    "server",
    "workspace",
    "mission",
)

LIFECYCLE_STATES: tuple[str, ...] = (
    "defined",
    "selected",
    "activated",
    "queued",
    "running",
    "waiting_for_approval",
    "blocked",
    "completed",
    "failed",
    "cancelled",
    "expired",
    "persisted",
    "cleaned_up",
)

ALLOWED_OUTPUT_FORMATS: tuple[str, ...] = (
    "structured_markdown",
    "json",
    "diff",
    "report",
    "artifact_bundle",
)

ALLOWED_CLEANUP_STRATEGIES: tuple[str, ...] = (
    "discard_context",
    "persist_summary_only",
    "persist_reviewable_artifacts",
)

SECRET_PERMISSION_FRAGMENTS: tuple[str, ...] = (
    ".env",
    "api_key",
    "credential",
    "password",
    "private_key",
    "seed_phrase",
    "secret",
    "token.raw",
    "wallet",
)


class SubAgentValidationError(ValueError):
    """Raised when a sub-agent preset or activation contract is invalid."""


def _as_list(data: Mapping[str, Any], key: str, *, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    value = data.get(key)
    if value is None:
        return default
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    raise SubAgentValidationError(f"{key} must be a list or string")


def _as_bool(data: Mapping[str, Any], key: str, *, default: bool = False) -> bool:
    value = data.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0"}:
            return False
    raise SubAgentValidationError(f"{key} must be boolean")


def _as_int(
    data: Mapping[str, Any],
    key: str,
    *,
    default: int,
    minimum: int = 0,
    maximum: int | None = None,
) -> int:
    raw = data.get(key, default)
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise SubAgentValidationError(f"{key} must be an integer") from exc
    if value < minimum:
        raise SubAgentValidationError(f"{key} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise SubAgentValidationError(f"{key} must be <= {maximum}")
    return value


def _mapping(data: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = data.get(key, {})
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise SubAgentValidationError(f"{key} must be a mapping")
    return value


def _get_any(data: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in data:
            return data[key]
    return default


def _contains_secret_fragment(value: str) -> bool:
    lowered = value.lower()
    return any(fragment in lowered for fragment in SECRET_PERMISSION_FRAGMENTS)


def _reject_secret_permissions(items: tuple[str, ...], field: str) -> None:
    blocked = [item for item in items if _contains_secret_fragment(item)]
    if blocked:
        raise SubAgentValidationError(f"{field} contains secret-shaped permissions: {blocked}")


def _require_fields(data: Mapping[str, Any], fields: tuple[str, ...]) -> None:
    missing = [field for field in fields if field not in data]
    if missing:
        raise SubAgentValidationError(f"preset missing required fields: {missing}")


@dataclass(frozen=True)
class SubAgentActivationPolicy:
    triggers: tuple[str, ...]
    manual_invocation_enabled: bool
    auto_activation_enabled: bool
    approval_required_for_activation: bool
    spawn_limit: int

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "SubAgentActivationPolicy":
        return cls(
            triggers=_as_list(data, "triggers"),
            manual_invocation_enabled=_as_bool(data, "manualInvocationEnabled", default=True),
            auto_activation_enabled=_as_bool(data, "autoActivationEnabled", default=False),
            approval_required_for_activation=_as_bool(
                data,
                "approvalRequiredForActivation",
                default=False,
            ),
            spawn_limit=_as_int(data, "spawnLimit", default=1, minimum=1, maximum=16),
        )


@dataclass(frozen=True)
class SubAgentToolPolicy:
    allowed: tuple[str, ...]
    denied: tuple[str, ...]
    requires_approval: tuple[str, ...]

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "SubAgentToolPolicy":
        policy = cls(
            allowed=_as_list(data, "allowed"),
            denied=_as_list(data, "denied"),
            requires_approval=_as_list(data, "requiresApproval"),
        )
        _reject_secret_permissions(policy.allowed, "tools.allowed")
        return policy


@dataclass(frozen=True)
class SubAgentMemoryPolicy:
    read: tuple[str, ...]
    write: tuple[str, ...]
    denied: tuple[str, ...]
    summarize_before_persist: bool

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "SubAgentMemoryPolicy":
        policy = cls(
            read=_as_list(data, "read"),
            write=_as_list(data, "write"),
            denied=_as_list(data, "denied"),
            summarize_before_persist=_as_bool(data, "summarizeBeforePersist", default=True),
        )
        _reject_secret_permissions(policy.read, "memory.read")
        _reject_secret_permissions(policy.write, "memory.write")
        if "*" in policy.write or "vault.writeAll" in policy.write:
            raise SubAgentValidationError("memory.write must not grant unrestricted vault writes")
        return policy


@dataclass(frozen=True)
class SubAgentComputeBudget:
    max_tokens: int
    max_runtime_ms: int
    max_parallel_workers: int
    max_retries: int
    max_iterations: int
    max_tool_calls: int
    priority: str
    allow_continuation: bool

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "SubAgentComputeBudget":
        normalized = {
            "maxTokens": _get_any(data, "maxTokens", "max_tokens", default=8000),
            "maxRuntimeMs": _get_any(data, "maxRuntimeMs", "max_runtime_ms", default=300000),
            "maxParallelWorkers": _get_any(data, "maxParallelWorkers", "max_parallel_workers", default=1),
            "maxRetries": _get_any(data, "maxRetries", "max_retries", default=0),
            "maxIterations": _get_any(data, "maxIterations", "max_iterations", default=8),
            "maxToolCalls": _get_any(data, "maxToolCalls", "max_tool_calls", default=12),
            "priority": _get_any(data, "priority", default="normal"),
            "allowContinuation": _get_any(data, "allowContinuation", "allow_continuation", default=False),
        }
        return cls(
            max_tokens=_as_int(normalized, "maxTokens", default=8000, minimum=1),
            max_runtime_ms=_as_int(normalized, "maxRuntimeMs", default=300000, minimum=1000),
            max_parallel_workers=_as_int(normalized, "maxParallelWorkers", default=1, minimum=1, maximum=16),
            max_retries=_as_int(normalized, "maxRetries", default=0, minimum=0, maximum=5),
            max_iterations=_as_int(normalized, "maxIterations", default=8, minimum=1, maximum=100),
            max_tool_calls=_as_int(normalized, "maxToolCalls", default=12, minimum=0, maximum=200),
            priority=str(normalized["priority"]),
            allow_continuation=_as_bool(normalized, "allowContinuation", default=False),
        )


@dataclass(frozen=True)
class SubAgentLifecyclePolicy:
    ttl_ms: int
    checkpoint_interval_ms: int
    max_checkpoints: int
    persist_final_summary: bool
    cleanup_strategy: str
    retain_artifacts: tuple[str, ...]

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "SubAgentLifecyclePolicy":
        cleanup_strategy = str(data.get("cleanupStrategy", "persist_summary_only"))
        if cleanup_strategy not in ALLOWED_CLEANUP_STRATEGIES:
            raise SubAgentValidationError(f"invalid cleanupStrategy: {cleanup_strategy!r}")
        return cls(
            ttl_ms=_as_int(data, "ttlMs", default=1800000, minimum=1000),
            checkpoint_interval_ms=_as_int(data, "checkpointIntervalMs", default=300000, minimum=1000),
            max_checkpoints=_as_int(data, "maxCheckpoints", default=4, minimum=0, maximum=100),
            persist_final_summary=_as_bool(data, "persistFinalSummary", default=True),
            cleanup_strategy=cleanup_strategy,
            retain_artifacts=_as_list(data, "retainArtifacts"),
        )


@dataclass(frozen=True)
class SubAgentOutputContract:
    format: str
    required_sections: tuple[str, ...]
    artifact_types: tuple[str, ...]
    schema_ref: str

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "SubAgentOutputContract":
        output_format = str(data.get("format", "structured_markdown"))
        if output_format not in ALLOWED_OUTPUT_FORMATS:
            raise SubAgentValidationError(f"invalid output format: {output_format!r}")
        return cls(
            format=output_format,
            required_sections=_as_list(data, "requiredSections"),
            artifact_types=_as_list(data, "artifactTypes"),
            schema_ref=str(data.get("schemaRef", "")),
        )


@dataclass(frozen=True)
class SubAgentPreset:
    id: str
    version: str
    name: str
    description: str
    role: str
    runtime_preferences: tuple[str, ...]
    modes: tuple[str, ...]
    activation: SubAgentActivationPolicy
    tools: SubAgentToolPolicy
    memory: SubAgentMemoryPolicy
    compute: SubAgentComputeBudget
    lifecycle: SubAgentLifecyclePolicy
    output: SubAgentOutputContract
    instructions: str
    source_path: str
    tags: tuple[str, ...]
    created_by: str
    metadata: dict[str, Any]

    @classmethod
    def from_mapping(
        cls,
        data: Mapping[str, Any],
        *,
        instructions: str = "",
        source_path: str = "",
    ) -> "SubAgentPreset":
        _require_fields(
            data,
            (
                "id",
                "version",
                "name",
                "description",
                "role",
                "runtimePreferences",
                "modes",
                "activation",
                "tools",
                "memory",
                "compute",
                "lifecycle",
                "output",
            ),
        )
        preset_id = str(data["id"])
        if " " in preset_id or not preset_id:
            raise SubAgentValidationError(f"invalid preset id: {preset_id!r}")

        runtime_preferences = _as_list(data, "runtimePreferences")
        invalid_runtimes = [value for value in runtime_preferences if value not in RUNTIME_BACKENDS]
        if invalid_runtimes:
            raise SubAgentValidationError(f"invalid runtimePreferences entries: {invalid_runtimes}")

        modes = _as_list(data, "modes")
        invalid_modes = [value for value in modes if value not in CHASE_OS_MODES]
        if invalid_modes:
            raise SubAgentValidationError(f"invalid modes entries: {invalid_modes}")

        known_keys = {
            "activation",
            "compute",
            "createdBy",
            "description",
            "id",
            "lifecycle",
            "memory",
            "modes",
            "name",
            "output",
            "role",
            "runtimePreferences",
            "tags",
            "tools",
            "version",
        }
        metadata = {key: value for key, value in data.items() if key not in known_keys}
        return cls(
            id=preset_id,
            version=str(data["version"]),
            name=str(data["name"]),
            description=str(data["description"]),
            role=str(data["role"]),
            runtime_preferences=runtime_preferences,
            modes=modes,
            activation=SubAgentActivationPolicy.from_mapping(_mapping(data, "activation")),
            tools=SubAgentToolPolicy.from_mapping(_mapping(data, "tools")),
            memory=SubAgentMemoryPolicy.from_mapping(_mapping(data, "memory")),
            compute=SubAgentComputeBudget.from_mapping(_mapping(data, "compute")),
            lifecycle=SubAgentLifecyclePolicy.from_mapping(_mapping(data, "lifecycle")),
            output=SubAgentOutputContract.from_mapping(_mapping(data, "output")),
            instructions=instructions.strip(),
            source_path=source_path,
            tags=_as_list(data, "tags"),
            created_by=str(data.get("createdBy", "ChaseOS")),
            metadata=dict(metadata),
        )

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


@dataclass(frozen=True)
class SubAgentActivationContext:
    activation_id: str
    preset_id: str
    preset_name: str
    task_id: str
    objective: str
    mode: str
    state: str
    selected_runtime: str
    selected_bus_name: str
    fallback_runtimes: tuple[str, ...]
    unavailable_preferences: tuple[str, ...]
    blocked_reasons: tuple[str, ...]
    activation_reason: str
    is_task_scoped: bool
    daemon_started: bool
    parent_agent_id: str
    mission_id: str
    created_at: str
    expires_at: str
    input_summary: str
    tool_policy: SubAgentToolPolicy
    memory_policy: SubAgentMemoryPolicy
    compute_budget: SubAgentComputeBudget
    lifecycle_policy: SubAgentLifecyclePolicy
    output_contract: SubAgentOutputContract
    source_path: str

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


def _json_ready(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    return value
