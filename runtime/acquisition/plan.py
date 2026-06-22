"""Acquisition plan schema and validator for Pass 1A."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .source_classes import ALLOWED_SOURCE_CLASSES, default_surface_for_source_class
from .validators import (
    ALLOWED_METHODS,
    ALLOWED_SURFACES,
    ALLOWED_TRIGGERS,
    AcquisitionValidationError,
    validate_browser_scope,
    validate_network_scope,
    validate_output_root,
    validate_relative_path,
    validate_source_path_for_class,
    validate_write_path,
)


def _slug(value: str, fallback: str = "acquisition-plan") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (slug or fallback)[:72]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class AcquirerIdentity:
    identity: str
    runtime_id: str = "source_pack_builder"
    trust_tier_ceiling: int = 2
    adapter_id: str | None = None
    role_card: str = "source-pack-builder"


@dataclass(frozen=True)
class AcquisitionScope:
    read_scope: list[str] = field(default_factory=list)
    browser_scope: list[Any] = field(default_factory=list)
    network_scope: list[Any] = field(default_factory=list)


@dataclass(frozen=True)
class FreshnessPolicy:
    default_window: str = "unknown"
    staleness_policy: str = "warn"
    time_sensitive_domain: str = "none"
    expires_after_hours: int | None = None


@dataclass(frozen=True)
class TrustPolicy:
    trust_floor: int = 4
    default_actionability: str = "briefing_only"
    handling_hints: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class OutputTargets:
    pack_root: str
    log_summary_dir: str | None = None
    latest_pointer_path: str | None = None  # when set, builder writes a latest-pack pointer here


@dataclass(frozen=True)
class PromotionDefaults:
    status: str = "workspace-local"
    allowed_next_steps: list[str] = field(default_factory=lambda: ["operator_review", "sbp_input"])
    canonical_mutation_allowed: bool = False


@dataclass(frozen=True)
class AuditRequirements:
    audit_required: bool = True
    activity_log_ref: str | None = None
    record_source_hashes: bool = True


@dataclass(frozen=True)
class AcquisitionSource:
    source_id: str
    source_class: str
    surface: str
    acquisition_method: str
    path: str
    display_name: str
    sidecar_path: str | None = None
    origin_kind: str | None = None
    declared_url: str | None = None
    base_trust_tier: int = 4
    confidence: str = "unknown"
    quality_marker: str = "unverified"
    operator_approval_state: str = "not_required"
    actionability: str = "briefing_only"
    freshness_window: str = "unknown"
    source_event_at: str | None = None
    captured_at: str | None = None
    expires_at: str | None = None
    staleness_policy: str = "warn"
    time_sensitive_domain: str = "none"
    source_quality_notes: list[str] = field(default_factory=list)
    contradiction_refs: list[str] = field(default_factory=list)
    audit_ref: str | None = None


@dataclass(frozen=True)
class AcquisitionPlan:
    plan_id: str
    objective: str
    requested_by: str
    downstream_target: str
    acquisition_surfaces: list[str]
    acquisition_methods: list[str]
    acquirer: AcquirerIdentity
    scope: AcquisitionScope
    trigger: str
    freshness_policy: FreshnessPolicy
    trust_policy: TrustPolicy
    output_targets: OutputTargets
    promotion: PromotionDefaults
    audit: AuditRequirements
    sources: list[AcquisitionSource]
    created_at: str = field(default_factory=_utc_now)


def _as_list(value: Any) -> list[Any]:
    if value in (None, ""):
        return []
    return value if isinstance(value, list) else [value]


def _derive_surface(source_class: str) -> str:
    try:
        return default_surface_for_source_class(source_class)
    except ValueError:
        return "manual_drop_in"


def _source_from_raw(raw: dict[str, Any], index: int, freshness_policy: FreshnessPolicy, trust_policy: TrustPolicy) -> AcquisitionSource:
    source_class = str(raw.get("source_class") or "").strip()
    source_id = str(raw.get("source_id") or f"source-{index:03d}").strip()
    surface = str(raw.get("surface") or _derive_surface(source_class)).strip()
    method = str(raw.get("acquisition_method") or "direct_file_read").strip()
    path = validate_relative_path(str(raw.get("path") or ""), f"sources[{index}].path")
    sidecar_path = raw.get("sidecar_path")
    sidecar_path = validate_relative_path(str(sidecar_path), f"sources[{index}].sidecar_path") if sidecar_path else None

    if surface not in ALLOWED_SURFACES:
        raise AcquisitionValidationError(f"sources[{index}].surface {surface!r} is not allowed in Pass 1A")
    if method not in ALLOWED_METHODS:
        raise AcquisitionValidationError(f"sources[{index}].acquisition_method {method!r} is not allowed in Pass 1A")
    if source_class not in ALLOWED_SOURCE_CLASSES:
        raise AcquisitionValidationError(f"sources[{index}].source_class {source_class!r} is not allowed")

    validate_source_path_for_class(path, source_class)
    base_trust_tier = int(raw.get("base_trust_tier", trust_policy.trust_floor))
    if not 1 <= base_trust_tier <= 4:
        raise AcquisitionValidationError(f"sources[{index}].base_trust_tier must be between 1 and 4")
    if base_trust_tier > trust_policy.trust_floor:
        raise AcquisitionValidationError(
            f"sources[{index}] base_trust_tier {base_trust_tier} is below trust_floor {trust_policy.trust_floor}"
        )

    if raw.get("connector_call_requested") or raw.get("api_call_requested") or raw.get("network_call_requested"):
        raise AcquisitionValidationError("Pass 1A sources may not request connector/API/network calls")
    if raw.get("allow_forms") or raw.get("allow_downloads") or raw.get("allow_credentials"):
        raise AcquisitionValidationError("Pass 1A sources may not allow browser forms, downloads, or credentials")

    return AcquisitionSource(
        source_id=source_id,
        source_class=source_class,
        surface=surface,
        acquisition_method=method,
        path=path,
        display_name=str(raw.get("display_name") or Path(path).name),
        sidecar_path=sidecar_path,
        origin_kind=raw.get("origin_kind"),
        declared_url=raw.get("declared_url"),
        base_trust_tier=base_trust_tier,
        confidence=str(raw.get("confidence") or "unknown"),
        quality_marker=str(raw.get("quality_marker") or "unverified"),
        operator_approval_state=str(raw.get("operator_approval_state") or "not_required"),
        actionability=str(raw.get("actionability") or trust_policy.default_actionability),
        freshness_window=str(raw.get("freshness_window") or freshness_policy.default_window),
        source_event_at=raw.get("source_event_at"),
        captured_at=raw.get("captured_at"),
        expires_at=raw.get("expires_at"),
        staleness_policy=str(raw.get("staleness_policy") or freshness_policy.staleness_policy),
        time_sensitive_domain=str(raw.get("time_sensitive_domain") or freshness_policy.time_sensitive_domain),
        source_quality_notes=list(raw.get("source_quality_notes") or []),
        contradiction_refs=list(raw.get("contradiction_refs") or []),
        audit_ref=raw.get("audit_ref"),
    )


def validate_acquisition_plan(raw: dict[str, Any]) -> AcquisitionPlan:
    """Validate and return an AcquisitionPlan object. Fail-closed."""
    if not isinstance(raw, dict):
        raise AcquisitionValidationError("acquisition plan must be a mapping")

    plan_id = _slug(str(raw.get("plan_id") or raw.get("pack_id") or ""), "acquisition-plan")
    objective_raw = raw.get("objective")
    if isinstance(objective_raw, dict):
        objective = str(objective_raw.get("title") or "").strip()
        requested_by = str(objective_raw.get("requested_by") or raw.get("requested_by") or "operator").strip()
        downstream_target = str(objective_raw.get("downstream_target") or raw.get("downstream_target") or "operator_review").strip()
    else:
        objective = str(objective_raw or "").strip()
        requested_by = str(raw.get("requested_by") or "operator").strip()
        downstream_target = str(raw.get("downstream_target") or "operator_review").strip()
    if not objective:
        raise AcquisitionValidationError("objective is required")

    if isinstance(raw.get("cadence"), dict):
        trigger_value = raw["cadence"].get("trigger") or raw.get("trigger") or "manual"
    else:
        trigger_value = raw.get("trigger") or "manual"
    trigger = str(trigger_value).strip()
    if trigger not in ALLOWED_TRIGGERS:
        raise AcquisitionValidationError(f"trigger {trigger!r} is not allowed in Pass 1A")

    freshness_raw = raw.get("freshness_policy") or {}
    freshness_policy = FreshnessPolicy(
        default_window=str(freshness_raw.get("default_window") or freshness_raw.get("freshness_window") or "unknown"),
        staleness_policy=str(freshness_raw.get("staleness_policy") or "warn"),
        time_sensitive_domain=str(freshness_raw.get("time_sensitive_domain") or "none"),
        expires_after_hours=freshness_raw.get("expires_after_hours"),
    )

    trust_raw = raw.get("trust") or raw.get("trust_policy") or {}
    trust_policy = TrustPolicy(
        trust_floor=int(trust_raw.get("trust_floor", 4)),
        default_actionability=str(trust_raw.get("default_actionability") or trust_raw.get("handling") or "briefing_only"),
        handling_hints=list(trust_raw.get("handling_hints") or []),
    )
    if not 1 <= trust_policy.trust_floor <= 4:
        raise AcquisitionValidationError("trust_floor must be between 1 and 4")

    runtime_raw = raw.get("runtime") or raw.get("acquirer") or {}
    acquirer = AcquirerIdentity(
        identity=str(runtime_raw.get("identity") or raw.get("acquirer_identity") or "source_pack_builder"),
        runtime_id=str(runtime_raw.get("runtime_id") or "source_pack_builder"),
        trust_tier_ceiling=int(runtime_raw.get("trust_tier_ceiling") or raw.get("acquirer_trust_tier_ceiling") or 2),
        adapter_id=runtime_raw.get("adapter_id") or raw.get("adapter_id"),
        role_card=str(runtime_raw.get("role_card") or "source-pack-builder"),
    )
    if not 1 <= acquirer.trust_tier_ceiling <= 4:
        raise AcquisitionValidationError("acquirer trust_tier_ceiling must be between 1 and 4")

    scope_raw = raw.get("scope") or {}
    read_scope = [validate_relative_path(str(item), "scope.read_scope[]") for item in _as_list(scope_raw.get("read_scope") or raw.get("read_scope"))]
    browser_scope = validate_browser_scope(scope_raw.get("browser_scope") if "browser_scope" in scope_raw else raw.get("browser_scope"))
    network_scope = validate_network_scope(scope_raw.get("network_scope") if "network_scope" in scope_raw else raw.get("network_scope"))

    sources_raw = raw.get("sources")
    if not isinstance(sources_raw, list) or not sources_raw:
        raise AcquisitionValidationError("sources must be a non-empty list")
    sources = [
        _source_from_raw(source, index, freshness_policy, trust_policy)
        for index, source in enumerate(sources_raw, start=1)
    ]
    for source in sources:
        if source.path not in read_scope:
            raise AcquisitionValidationError(f"source path {source.path!r} is not declared in read_scope")
        if source.sidecar_path and source.sidecar_path not in read_scope:
            raise AcquisitionValidationError(f"sidecar path {source.sidecar_path!r} is not declared in read_scope")

    acquisition_surfaces = [str(item).strip() for item in _as_list(raw.get("acquisition_surfaces")) if str(item).strip()]
    acquisition_methods = [str(item).strip() for item in _as_list(raw.get("acquisition_methods")) if str(item).strip()]
    if not acquisition_surfaces:
        acquisition_surfaces = sorted({source.surface for source in sources})
    if not acquisition_methods:
        acquisition_methods = sorted({source.acquisition_method for source in sources})
    unsupported_surfaces = set(acquisition_surfaces) - ALLOWED_SURFACES
    unsupported_methods = set(acquisition_methods) - ALLOWED_METHODS
    if unsupported_surfaces:
        raise AcquisitionValidationError(f"unsupported acquisition_surfaces: {sorted(unsupported_surfaces)}")
    if unsupported_methods:
        raise AcquisitionValidationError(f"unsupported acquisition_methods: {sorted(unsupported_methods)}")
    for source in sources:
        if source.surface not in acquisition_surfaces:
            raise AcquisitionValidationError(f"source surface {source.surface!r} not declared in acquisition_surfaces")
        if source.acquisition_method not in acquisition_methods:
            raise AcquisitionValidationError(f"source method {source.acquisition_method!r} not declared in acquisition_methods")

    output_raw = raw.get("output_targets") or {}
    pack_root_raw = output_raw.get("pack_root") or raw.get("write_target_root")
    if not pack_root_raw:
        date_prefix = raw.get("run_date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        pack_root_raw = f"runtime/acquisition/packs/{date_prefix}-{plan_id}"
    latest_pointer_path_raw = output_raw.get("latest_pointer_path")
    output_targets = OutputTargets(
        pack_root=validate_output_root(str(pack_root_raw)),
        log_summary_dir=validate_output_root(output_raw["log_summary_dir"], "output_targets.log_summary_dir")
        if output_raw.get("log_summary_dir")
        else None,
        latest_pointer_path=validate_write_path(str(latest_pointer_path_raw)) if latest_pointer_path_raw else None,
    )

    promotion_raw = raw.get("promotion") or {}
    promotion = PromotionDefaults(
        status=str(promotion_raw.get("status") or "workspace-local"),
        allowed_next_steps=list(promotion_raw.get("allowed_next_steps") or ["operator_review", "sbp_input"]),
        canonical_mutation_allowed=bool(promotion_raw.get("canonical_mutation_allowed", False)),
    )
    if promotion.canonical_mutation_allowed or raw.get("canonical_mutation_requested"):
        raise AcquisitionValidationError("canonical mutation is forbidden in Acquisition + Normalization Pass 1A")

    audit_raw = raw.get("audit") or {}
    audit = AuditRequirements(
        audit_required=bool(audit_raw.get("audit_required", True)),
        activity_log_ref=audit_raw.get("activity_log_ref"),
        record_source_hashes=bool(audit_raw.get("record_source_hashes", True)),
    )
    if not audit.audit_required:
        raise AcquisitionValidationError("audit.audit_required must be true in Pass 1A")

    if raw.get("connector_call_requested") or raw.get("api_call_requested") or raw.get("network_call_requested"):
        raise AcquisitionValidationError("Pass 1A acquisition plans may not request connector/API/network calls")
    for key in ("delivery_target", "mcp_surface", "native_cron"):
        if raw.get(key):
            raise AcquisitionValidationError(f"{key} is outside Acquisition + Normalization Pass 1A")

    return AcquisitionPlan(
        plan_id=plan_id,
        objective=objective,
        requested_by=requested_by,
        downstream_target=downstream_target,
        acquisition_surfaces=acquisition_surfaces,
        acquisition_methods=acquisition_methods,
        acquirer=acquirer,
        scope=AcquisitionScope(read_scope=read_scope, browser_scope=browser_scope, network_scope=network_scope),
        trigger=trigger,
        freshness_policy=freshness_policy,
        trust_policy=trust_policy,
        output_targets=output_targets,
        promotion=promotion,
        audit=audit,
        sources=sources,
        created_at=str(raw.get("created_at") or _utc_now()),
    )


def acquisition_plan_from_legacy_inputs(inputs: dict[str, Any]) -> AcquisitionPlan:
    """Accept the existing AOR source_pack_builder input shape."""
    sources = inputs.get("sources") or []
    read_scope: list[str] = []
    for source in sources:
        if isinstance(source, dict):
            if source.get("path"):
                read_scope.append(validate_relative_path(str(source["path"]), "sources[].path"))
            if source.get("sidecar_path"):
                read_scope.append(validate_relative_path(str(source["sidecar_path"]), "sources[].sidecar_path"))

    objective = inputs.get("objective")
    plan_raw = {
        "plan_id": inputs.get("plan_id") or inputs.get("pack_id"),
        "objective": objective,
        "project_scope": inputs.get("project_scope"),
        "runtime": {
            "identity": inputs.get("acquirer_identity") or "source_pack_builder",
            "trust_tier_ceiling": inputs.get("acquirer_trust_tier_ceiling") or 2,
            "adapter_id": inputs.get("adapter_id"),
        },
        "scope": {
            "read_scope": read_scope,
            "browser_scope": inputs.get("browser_scope") or [],
            "network_scope": inputs.get("network_scope") or [],
        },
        "sources": sources,
        "output_targets": {
            "pack_root": inputs.get("write_target_root"),
        },
        "trust": inputs.get("trust") or {"trust_floor": 4, "default_actionability": "briefing_only"},
        "freshness_policy": inputs.get("freshness_policy") or {},
        "promotion": inputs.get("promotion") or {"canonical_mutation_allowed": False},
        "audit": inputs.get("audit") or {"audit_required": True},
        "created_at": inputs.get("created_at"),
        "acquisition_surfaces": inputs.get("acquisition_surfaces"),
        "acquisition_methods": inputs.get("acquisition_methods"),
        "trigger": inputs.get("trigger") or "manual",
        "canonical_mutation_requested": inputs.get("canonical_mutation_requested"),
        "connector_call_requested": inputs.get("connector_call_requested"),
        "api_call_requested": inputs.get("api_call_requested"),
        "network_call_requested": inputs.get("network_call_requested"),
        "delivery_target": inputs.get("delivery_target"),
        "mcp_surface": inputs.get("mcp_surface"),
        "native_cron": inputs.get("native_cron"),
    }
    if isinstance(objective, dict):
        plan_raw["requested_by"] = objective.get("requested_by")
        plan_raw["downstream_target"] = objective.get("downstream_target")
    return validate_acquisition_plan(plan_raw)
