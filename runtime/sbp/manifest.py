"""
manifest.py — SBP Pipeline Manifest Contract (Phase 9 Pass 1A)

Defines the sbp_config block schema for Scheduled Briefing Pipeline manifests.
SBP pipelines are AOR workflows with task_type: scheduled-briefing.
The AOR manifest handles outer governance fields; sbp_config handles the
SBP-specific pipeline declaration (trigger, adapters, guardrail).

All validation is fail-closed: invalid configs raise SBPManifestValidationError
before any pipeline execution begins.

Public API:
    validate_sbp_config(sbp_config_dict, pipeline_id) -> SBPConfig
    load_sbp_config(aor_manifest_dict) -> SBPConfig
    SBPManifestValidationError
    SBPConfig, SBPTriggerConfig, SBPInputAdapterConfig,
    SBPDeliveryAdapterConfig, SBPGuardrailConfig
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ── Valid values ───────────────────────────────────────────────────────────────

FORBIDDEN_PERMISSION_CEILINGS: frozenset[str] = frozenset({
    "protected_file_writes",
    "canonical_promotion",
})

VALID_TRIGGER_TYPES: frozenset[str] = frozenset({
    "cron", "event", "manual", "webhook",
})

VALID_DELIVERY_ADAPTER_TYPES: frozenset[str] = frozenset({
    "vault-local", "discord", "email", "whop", "slack",
})

VALID_INPUT_ADAPTER_TYPES: frozenset[str] = frozenset({
    "vault-notes", "sic-workspace", "external-api", "agent-activity", "raw-digest",
    "acquisition-pack",
})

VALID_HUMAN_IN_LOOP: frozenset[str] = frozenset({
    "required", "optional", "none",
})

VALID_FAIL_BEHAVIORS: frozenset[str] = frozenset({
    "halt_and_log", "retry_once", "notify_user",
})

VALID_EXECUTION_ADAPTERS: frozenset[str] = frozenset({
    "openclaw",   # primary operator synthesizer — runtime/openclaw/model_config.yaml
    "hermes",     # planning/review runtime — runtime/hermes/model_config.yaml
    "claude",     # legacy label — resolves to openclaw (see ADAPTER_TO_RUNTIME in execute.py)
})


# ── Dataclasses ────────────────────────────────────────────────────────────────

@dataclass
class SBPTriggerConfig:
    type: str
    cron_expression: Optional[str] = None
    timezone: Optional[str] = None
    max_runs_per_day: int = 1


@dataclass
class SBPInputAdapterConfig:
    type: str
    trust_tier: int = 3
    paths: list[str] = field(default_factory=list)
    workspace_id: Optional[str] = None
    pack_path: Optional[str] = None         # acquisition-pack: static path to briefing_ready_input_set.json
    pack_latest_path: Optional[str] = None  # acquisition-pack: path to latest-pointer file (dynamic resolution)
    optional: bool = False                  # acquisition-pack: if True, missing pack returns stub instead of failing


@dataclass
class SBPDeliveryAdapterConfig:
    type: str
    channel_hint: Optional[str] = None     # advisory only — no credentials here
    webhook_env_var: Optional[str] = None  # per-pipeline env var name for webhook URL / API key
    channel_id: Optional[str] = None       # target channel/forum ID (e.g. Whop forum_experience_id)
    draft_review_required: bool = False    # if True, writes draft to vault; operator must promote


@dataclass
class SBPGuardrailConfig:
    permission_ceiling: str
    write_scope: list[str] = field(default_factory=list)
    read_scope: list[str] = field(default_factory=list)
    human_in_loop: str = "optional"
    fail_behavior: str = "halt_and_log"
    audit_required: bool = True
    max_token_budget: Optional[int] = None


@dataclass
class SBPConfig:
    trigger: SBPTriggerConfig
    input_adapters: list[SBPInputAdapterConfig]
    execution_adapter: str
    delivery_adapters: list[SBPDeliveryAdapterConfig]
    guardrail: SBPGuardrailConfig


# ── Validation error ───────────────────────────────────────────────────────────

class SBPManifestValidationError(ValueError):
    """Raised when sbp_config validation fails. Fail-closed."""


# ── Validators ─────────────────────────────────────────────────────────────────

def validate_sbp_config(sbp_config_dict: dict, pipeline_id: str = "") -> SBPConfig:
    """Validate sbp_config block. Raise SBPManifestValidationError on any violation."""
    if not isinstance(sbp_config_dict, dict):
        raise SBPManifestValidationError(
            f"sbp_config for '{pipeline_id}' must be a mapping, "
            f"got {type(sbp_config_dict).__name__}"
        )

    required = {"trigger", "input_adapters", "execution_adapter", "delivery_adapters", "guardrail"}
    missing = required - set(sbp_config_dict)
    if missing:
        raise SBPManifestValidationError(
            f"sbp_config for '{pipeline_id}' missing required keys: {sorted(missing)}"
        )

    # Trigger
    trigger_dict = sbp_config_dict["trigger"]
    if not isinstance(trigger_dict, dict):
        raise SBPManifestValidationError(
            f"sbp_config.trigger must be a mapping for '{pipeline_id}'"
        )
    trigger_type = trigger_dict.get("type", "")
    if trigger_type not in VALID_TRIGGER_TYPES:
        raise SBPManifestValidationError(
            f"sbp_config.trigger.type '{trigger_type}' invalid for '{pipeline_id}'; "
            f"must be one of {sorted(VALID_TRIGGER_TYPES)}"
        )
    if trigger_type == "cron" and not trigger_dict.get("cron_expression"):
        raise SBPManifestValidationError(
            f"sbp_config.trigger.cron_expression required when trigger.type='cron' "
            f"for '{pipeline_id}'"
        )
    trigger = SBPTriggerConfig(
        type=trigger_type,
        cron_expression=trigger_dict.get("cron_expression"),
        timezone=trigger_dict.get("timezone"),
        max_runs_per_day=int(trigger_dict.get("max_runs_per_day", 1)),
    )

    # Input adapters
    raw_inputs = sbp_config_dict["input_adapters"]
    if not isinstance(raw_inputs, list):
        raise SBPManifestValidationError(
            f"sbp_config.input_adapters must be a list for '{pipeline_id}'"
        )
    input_adapters: list[SBPInputAdapterConfig] = []
    for i, ia in enumerate(raw_inputs):
        if not isinstance(ia, dict):
            raise SBPManifestValidationError(
                f"sbp_config.input_adapters[{i}] must be a mapping for '{pipeline_id}'"
            )
        ia_type = ia.get("type", "")
        if ia_type not in VALID_INPUT_ADAPTER_TYPES:
            raise SBPManifestValidationError(
                f"sbp_config.input_adapters[{i}].type '{ia_type}' invalid for '{pipeline_id}'; "
                f"must be one of {sorted(VALID_INPUT_ADAPTER_TYPES)}"
            )
        trust_tier = int(ia.get("trust_tier", 3))
        if not 1 <= trust_tier <= 4:
            raise SBPManifestValidationError(
                f"sbp_config.input_adapters[{i}].trust_tier {trust_tier} out of range "
                f"[1-4] for '{pipeline_id}'"
            )
        input_adapters.append(SBPInputAdapterConfig(
            type=ia_type,
            trust_tier=trust_tier,
            paths=list(ia.get("paths", [])),
            workspace_id=ia.get("workspace_id"),
            pack_path=ia.get("pack_path") or None,
            pack_latest_path=ia.get("pack_latest_path") or None,
            optional=bool(ia.get("optional", False)),
        ))

    # Execution adapter
    execution_adapter = str(sbp_config_dict["execution_adapter"]).strip()
    if not execution_adapter:
        raise SBPManifestValidationError(
            f"sbp_config.execution_adapter must be non-empty for '{pipeline_id}'"
        )
    if execution_adapter not in VALID_EXECUTION_ADAPTERS:
        raise SBPManifestValidationError(
            f"sbp_config.execution_adapter '{execution_adapter}' is not a known runtime for '{pipeline_id}'; "
            f"must be one of {sorted(VALID_EXECUTION_ADAPTERS)}"
        )

    # Delivery adapters
    raw_delivery = sbp_config_dict["delivery_adapters"]
    if not isinstance(raw_delivery, list):
        raise SBPManifestValidationError(
            f"sbp_config.delivery_adapters must be a list for '{pipeline_id}'"
        )
    delivery_adapters: list[SBPDeliveryAdapterConfig] = []
    for i, da in enumerate(raw_delivery):
        if not isinstance(da, dict):
            raise SBPManifestValidationError(
                f"sbp_config.delivery_adapters[{i}] must be a mapping for '{pipeline_id}'"
            )
        da_type = da.get("type", "")
        if da_type not in VALID_DELIVERY_ADAPTER_TYPES:
            raise SBPManifestValidationError(
                f"sbp_config.delivery_adapters[{i}].type '{da_type}' invalid for '{pipeline_id}'; "
                f"must be one of {sorted(VALID_DELIVERY_ADAPTER_TYPES)}"
            )
        delivery_adapters.append(SBPDeliveryAdapterConfig(
            type=da_type,
            channel_hint=da.get("channel_hint"),
            webhook_env_var=da.get("webhook_env_var") or None,
            channel_id=da.get("channel_id") or None,
            draft_review_required=bool(da.get("draft_review_required", False)),
        ))

    # Guardrail
    g = sbp_config_dict["guardrail"]
    if not isinstance(g, dict):
        raise SBPManifestValidationError(
            f"sbp_config.guardrail must be a mapping for '{pipeline_id}'"
        )
    ceiling = g.get("permission_ceiling", "")
    if not ceiling:
        raise SBPManifestValidationError(
            f"sbp_config.guardrail.permission_ceiling required for '{pipeline_id}'"
        )
    if ceiling in FORBIDDEN_PERMISSION_CEILINGS:
        raise SBPManifestValidationError(
            f"sbp_config.guardrail.permission_ceiling '{ceiling}' is forbidden for SBP pipelines; "
            f"SBP pipelines may not request: {sorted(FORBIDDEN_PERMISSION_CEILINGS)}"
        )
    hil = g.get("human_in_loop", "optional")
    if hil not in VALID_HUMAN_IN_LOOP:
        raise SBPManifestValidationError(
            f"sbp_config.guardrail.human_in_loop '{hil}' invalid for '{pipeline_id}'; "
            f"must be one of {sorted(VALID_HUMAN_IN_LOOP)}"
        )
    fb = g.get("fail_behavior", "halt_and_log")
    if fb not in VALID_FAIL_BEHAVIORS:
        raise SBPManifestValidationError(
            f"sbp_config.guardrail.fail_behavior '{fb}' invalid for '{pipeline_id}'; "
            f"must be one of {sorted(VALID_FAIL_BEHAVIORS)}"
        )
    max_budget = g.get("max_token_budget")
    guardrail = SBPGuardrailConfig(
        permission_ceiling=ceiling,
        write_scope=list(g.get("write_scope", [])),
        read_scope=list(g.get("read_scope", [])),
        human_in_loop=hil,
        fail_behavior=fb,
        audit_required=bool(g.get("audit_required", True)),
        max_token_budget=int(max_budget) if max_budget is not None else None,
    )

    return SBPConfig(
        trigger=trigger,
        input_adapters=input_adapters,
        execution_adapter=execution_adapter,
        delivery_adapters=delivery_adapters,
        guardrail=guardrail,
    )


def load_sbp_config(manifest: dict) -> SBPConfig:
    """Extract and validate sbp_config from an AOR manifest dict. Fail-closed."""
    pipeline_id = manifest.get("id", "unknown")
    sbp_config_dict = manifest.get("sbp_config")
    if sbp_config_dict is None:
        raise SBPManifestValidationError(
            f"manifest '{pipeline_id}' with task_type 'scheduled-briefing' "
            f"must have an 'sbp_config' block"
        )
    return validate_sbp_config(sbp_config_dict, pipeline_id)
