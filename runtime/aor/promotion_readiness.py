"""promotion_readiness.py — focused readiness helpers for runtime-instance promotion paths."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from runtime.aor.registry import load_manifest
from runtime.aor.role_cards import load_card
from runtime.gate_interface import load_adapter_manifest


PROMOTION_RECORD_LANE = "07_LOGS/Promotion-Records/"
PROMOTION_RECORD_INDEX = "07_LOGS/Promotion-Records/Promotion-Records-Index.md"
PROMOTION_RECORD_GUIDE = "07_LOGS/Promotion-Records/PROMOTION-RECORDS-Folder-Guide.md"


def _detect_vault_root() -> Path:
    here = Path(__file__).resolve()
    vault_root = here.parents[2]
    if not (vault_root / "CLAUDE.md").exists():
        raise RuntimeError(
            f"Could not detect vault root. Expected CLAUDE.md at: {vault_root}\n"
            "Use vault_root parameter to specify the vault path explicitly."
        )
    return vault_root


def collect_openclaw_preactivation_failure_signals(
    vault_root: Optional[Path] = None,
) -> dict:
    """Return declared OpenClaw pre-activation failure-path signals.

    This helper stays contract/readiness-oriented. It does not execute the
    workflow and does not imply activation readiness. It only answers whether
    the draft contract currently declares the main pre-activation failure paths
    that future activation review would need to preserve.
    """
    if vault_root is None:
        vault_root = _detect_vault_root()

    workflow_manifest = load_manifest("openclaw_promote_note", vault_root=vault_root)
    role_card = load_card("openclaw-promotion-review", vault_root=vault_root)

    blocking_gaps: list[str] = []
    if workflow_manifest is None or role_card is None:
        return {
            "approval_linkage_declared": False,
            "target_scope_failure_declared": False,
            "audit_survival_declared": False,
            "operator_approval_ref_required": False,
            "blocking_gaps": [
                "OpenClaw draft promotion workflow/role-card substrate is missing, so failure-path signals cannot be evaluated."
            ],
        }

    workflow_inputs = set(workflow_manifest.get("inputs", []))
    workflow_outputs = set(workflow_manifest.get("outputs", []))
    escalation_rules = set(role_card.get("escalation_rules", []))
    runtime_expectations = set(role_card.get("runtime_expectations", []))
    approval_rule = workflow_manifest.get("approval_rule")

    operator_approval_ref_required = "operator_approval_ref" in workflow_inputs
    approval_linkage_declared = (
        operator_approval_ref_required
        and approval_rule == "operator-explicit"
        and "approval missing or invalid" in escalation_rules
    )
    if not approval_linkage_declared:
        blocking_gaps.append(
            "OpenClaw draft contract does not fully declare approval-linkage failure posture."
        )

    target_scope_failure_declared = (
        "target_path" in workflow_inputs
        and "02_KNOWLEDGE/" in set(role_card.get("write_scope", []))
        and "target outside declared canonical scope" in escalation_rules
    )
    if not target_scope_failure_declared:
        blocking_gaps.append(
            "OpenClaw draft contract does not fully declare exact target-scope failure posture."
        )

    audit_survival_declared = (
        "audit_record_path" in workflow_outputs
        and "07_LOGS/Agent-Activity/" in set(workflow_manifest.get("writeback_targets", []))
        and "audit record is written regardless of approval outcome" in runtime_expectations
    )
    if not audit_survival_declared:
        blocking_gaps.append(
            "OpenClaw draft contract does not fully declare audit-survival expectations for blocked runs."
        )

    return {
        "approval_linkage_declared": approval_linkage_declared,
        "target_scope_failure_declared": target_scope_failure_declared,
        "audit_survival_declared": audit_survival_declared,
        "operator_approval_ref_required": operator_approval_ref_required,
        "blocking_gaps": blocking_gaps,
    }


def assess_openclaw_promotion_activation_readiness(
    vault_root: Optional[Path] = None,
) -> dict:
    """Return the current OpenClaw promotion activation-readiness posture.

    This is intentionally a read-only evaluation helper for the OpenClaw-first
    readiness gate. It does not activate any workflow or mutate policy.
    """
    if vault_root is None:
        vault_root = _detect_vault_root()

    adapter_manifest = load_adapter_manifest("openclaw")
    workflow_manifest = load_manifest("openclaw_promote_note", vault_root=vault_root)
    role_card = load_card("openclaw-promotion-review", vault_root=vault_root)

    blocking_issues: list[str] = []

    promotion_behavior = adapter_manifest.get("promotion_behavior", {}) if adapter_manifest else {}
    denied_targets = set(adapter_manifest.get("explicitly_denied_write_targets", [])) if adapter_manifest else set()

    adapter_posture_ok = (
        bool(adapter_manifest)
        and promotion_behavior.get("may_promote_to_knowledge") == "no"
        and promotion_behavior.get("gate_conditions_required") is False
        and "02_KNOWLEDGE/**" in denied_targets
    )
    adapter_still_fail_closed = adapter_posture_ok
    if not adapter_posture_ok:
        blocking_issues.append(
            "OpenClaw adapter manifest no longer reflects the expected fail-closed promotion posture."
        )

    provenance_gate_seam_present = True
    if workflow_manifest is None or role_card is None:
        provenance_gate_seam_present = False
        blocking_issues.append(
            "OpenClaw draft promotion workflow/role-card substrate is missing, so readiness cannot be evaluated."
        )
    else:
        workflow_inputs = set(workflow_manifest.get("inputs", []))
        escalation_rules = set(role_card.get("escalation_rules", []))
        runtime_expectations = set(role_card.get("runtime_expectations", []))
        provenance_gate_seam_present = (
            "verification_status" in workflow_inputs
            and "provenance minimums fail" in escalation_rules
            and "Gate provenance minimums are checked centrally" in runtime_expectations
        )
        if not provenance_gate_seam_present:
            blocking_issues.append(
                "OpenClaw draft promotion substrate does not yet preserve the centralized provenance-minimum gate seam."
            )

    promotion_record_lane_seeded = all(
        (vault_root / rel_path).exists()
        for rel_path in (
            PROMOTION_RECORD_LANE,
            PROMOTION_RECORD_INDEX,
            PROMOTION_RECORD_GUIDE,
        )
    )
    if not promotion_record_lane_seeded:
        blocking_issues.append(
            "07_LOGS/Promotion-Records/ routing substrate is missing on disk."
        )

    promotion_record_lane_declared = False
    workflow_still_draft = False
    if workflow_manifest is not None and role_card is not None:
        workflow_targets = set(workflow_manifest.get("writeback_targets", []))
        role_write_scope = set(role_card.get("write_scope", []))
        role_allowed_actions = set(role_card.get("allowed_actions", []))
        workflow_still_draft = workflow_manifest.get("status") == "draft"
        promotion_record_lane_declared = (
            PROMOTION_RECORD_LANE in workflow_targets
            and PROMOTION_RECORD_LANE in role_write_scope
            and "write_promotion_record" in role_allowed_actions
        )
    if not promotion_record_lane_declared:
        blocking_issues.append(
            "Promotion-Records lane is not yet declared in the OpenClaw promotion workflow/role-card contract."
        )

    if workflow_still_draft:
        blocking_issues.append(
            "OpenClaw promotion workflow remains status='draft', so activation readiness must stay blocked."
        )

    if adapter_still_fail_closed:
        blocking_issues.append(
            "OpenClaw adapter manifest still keeps may_promote_to_knowledge='no' and gate_conditions_required=false, so activation readiness remains intentionally fail-closed."
        )

    ready = (
        adapter_posture_ok
        and provenance_gate_seam_present
        and promotion_record_lane_seeded
        and promotion_record_lane_declared
        and not workflow_still_draft
        and not adapter_still_fail_closed
    )

    return {
        "ready": ready,
        "adapter_posture_ok": adapter_posture_ok,
        "adapter_still_fail_closed": adapter_still_fail_closed,
        "provenance_gate_seam_present": provenance_gate_seam_present,
        "promotion_record_lane_seeded": promotion_record_lane_seeded,
        "promotion_record_lane_declared": promotion_record_lane_declared,
        "workflow_still_draft": workflow_still_draft,
        "blocking_issues": blocking_issues,
    }


def collect_hermes_preactivation_failure_signals(
    vault_root: Optional[Path] = None,
) -> dict:
    """Return declared Hermes pre-activation failure-path signals.

    This helper stays contract/readiness-oriented. It does not execute the
    workflow and does not imply activation readiness. It only answers whether
    the draft Hermes contract currently declares the main pre-activation
    failure paths that future activation review would need to preserve.
    """
    if vault_root is None:
        vault_root = _detect_vault_root()

    workflow_manifest = load_manifest("hermes_promote_note", vault_root=vault_root)
    role_card = load_card("hermes-promotion-review", vault_root=vault_root)

    blocking_gaps: list[str] = []
    if workflow_manifest is None or role_card is None:
        return {
            "approval_linkage_declared": False,
            "control_plane_linkage_declared": False,
            "direct_authority_guard_declared": False,
            "target_scope_failure_declared": False,
            "audit_survival_declared": False,
            "operator_approval_ref_required": False,
            "control_plane_request_ref_required": False,
            "blocking_gaps": [
                "Hermes draft promotion workflow/role-card substrate is missing, so failure-path signals cannot be evaluated."
            ],
        }

    workflow_inputs = set(workflow_manifest.get("inputs", []))
    workflow_outputs = set(workflow_manifest.get("outputs", []))
    escalation_rules = set(role_card.get("escalation_rules", []))
    runtime_expectations = set(role_card.get("runtime_expectations", []))
    forbidden_actions = set(role_card.get("forbidden_actions", []))
    approval_rule = workflow_manifest.get("approval_rule")
    allowed_actions = set(role_card.get("allowed_actions", []))
    write_scope = set(role_card.get("write_scope", []))
    writeback_targets = set(workflow_manifest.get("writeback_targets", []))

    operator_approval_ref_required = "operator_approval_ref" in workflow_inputs
    control_plane_request_ref_required = "control_plane_request_ref" in workflow_inputs

    approval_linkage_declared = (
        operator_approval_ref_required
        and approval_rule == "operator-explicit"
        and "approval envelope missing or invalid" in escalation_rules
    )
    if not approval_linkage_declared:
        blocking_gaps.append(
            "Hermes draft contract does not fully declare approval-linkage failure posture."
        )

    control_plane_linkage_declared = (
        control_plane_request_ref_required
        and "read_control_plane_approval_record" in allowed_actions
        and "approval envelope missing or invalid" in escalation_rules
    )
    if not control_plane_linkage_declared:
        blocking_gaps.append(
            "Hermes draft contract does not fully declare control-plane linkage failure posture."
        )

    direct_authority_guard_declared = (
        "direct_discord_driven_write" in forbidden_actions
        and "Discord/control-plane input treated as direct authority" in escalation_rules
        and "Discord/gateway input remains control-plane context, not direct authority" in runtime_expectations
    )
    if not direct_authority_guard_declared:
        blocking_gaps.append(
            "Hermes draft contract does not fully declare direct-authority denial for Discord/control-plane input."
        )

    target_scope_failure_declared = (
        "target_path" in workflow_inputs
        and "02_KNOWLEDGE/" in write_scope
        and "target outside declared canonical scope" in escalation_rules
    )
    if not target_scope_failure_declared:
        blocking_gaps.append(
            "Hermes draft contract does not fully declare exact target-scope failure posture."
        )

    audit_survival_declared = (
        "audit_record_path" in workflow_outputs
        and "07_LOGS/Agent-Activity/" in writeback_targets
        and "audit record is written regardless of approval outcome" in runtime_expectations
    )
    if not audit_survival_declared:
        blocking_gaps.append(
            "Hermes draft contract does not fully declare audit-survival expectations for blocked runs."
        )

    return {
        "approval_linkage_declared": approval_linkage_declared,
        "control_plane_linkage_declared": control_plane_linkage_declared,
        "direct_authority_guard_declared": direct_authority_guard_declared,
        "target_scope_failure_declared": target_scope_failure_declared,
        "audit_survival_declared": audit_survival_declared,
        "operator_approval_ref_required": operator_approval_ref_required,
        "control_plane_request_ref_required": control_plane_request_ref_required,
        "blocking_gaps": blocking_gaps,
    }


def assess_hermes_promotion_activation_readiness(
    vault_root: Optional[Path] = None,
) -> dict:
    """Return the current Hermes promotion activation-readiness posture.

    This is intentionally a read-only evaluation helper for the Hermes-side
    readiness gate. It does not activate any workflow or mutate policy.
    """
    if vault_root is None:
        vault_root = _detect_vault_root()

    adapter_manifest = load_adapter_manifest("hermes")
    workflow_manifest = load_manifest("hermes_promote_note", vault_root=vault_root)
    role_card = load_card("hermes-promotion-review", vault_root=vault_root)

    blocking_issues: list[str] = []

    promotion_behavior = adapter_manifest.get("promotion_behavior", {}) if adapter_manifest else {}
    denied_targets = set(adapter_manifest.get("explicitly_denied_write_targets", [])) if adapter_manifest else set()

    adapter_posture_ok = (
        bool(adapter_manifest)
        and promotion_behavior.get("may_promote_to_knowledge") == "no"
        and promotion_behavior.get("gate_conditions_required") is False
        and "02_KNOWLEDGE/**" in denied_targets
    )
    adapter_still_fail_closed = adapter_posture_ok
    if not adapter_posture_ok:
        blocking_issues.append(
            "Hermes adapter manifest no longer reflects the expected fail-closed promotion posture."
        )

    provenance_gate_seam_present = True
    control_plane_linkage_declared = False
    direct_authority_guard_declared = False
    if workflow_manifest is None or role_card is None:
        provenance_gate_seam_present = False
        blocking_issues.append(
            "Hermes draft promotion workflow/role-card substrate is missing, so readiness cannot be evaluated."
        )
    else:
        workflow_inputs = set(workflow_manifest.get("inputs", []))
        escalation_rules = set(role_card.get("escalation_rules", []))
        runtime_expectations = set(role_card.get("runtime_expectations", []))
        forbidden_actions = set(role_card.get("forbidden_actions", []))

        provenance_gate_seam_present = (
            "verification_status" in workflow_inputs
            and "provenance minimums fail" in escalation_rules
            and "Gate provenance minimums are checked centrally" in runtime_expectations
        )
        control_plane_linkage_declared = (
            "control_plane_request_ref" in workflow_inputs
            and "approval envelope missing or invalid" in escalation_rules
            and "read_control_plane_approval_record" in set(role_card.get("allowed_actions", []))
        )
        direct_authority_guard_declared = (
            "direct_discord_driven_write" in forbidden_actions
            and "Discord/control-plane input treated as direct authority" in escalation_rules
            and "Discord/gateway input remains control-plane context, not direct authority" in runtime_expectations
        )
        if not provenance_gate_seam_present:
            blocking_issues.append(
                "Hermes draft promotion substrate does not yet preserve the centralized provenance-minimum gate seam."
            )
        if not control_plane_linkage_declared:
            blocking_issues.append(
                "Hermes draft promotion substrate does not yet fully declare control-plane approval linkage."
            )
        if not direct_authority_guard_declared:
            blocking_issues.append(
                "Hermes draft promotion substrate does not yet fully declare direct-authority denial for Discord/control-plane input."
            )

    promotion_record_lane_seeded = all(
        (vault_root / rel_path).exists()
        for rel_path in (
            PROMOTION_RECORD_LANE,
            PROMOTION_RECORD_INDEX,
            PROMOTION_RECORD_GUIDE,
        )
    )
    if not promotion_record_lane_seeded:
        blocking_issues.append(
            "07_LOGS/Promotion-Records/ routing substrate is missing on disk."
        )

    promotion_record_lane_declared = False
    workflow_still_draft = False
    if workflow_manifest is not None and role_card is not None:
        workflow_targets = set(workflow_manifest.get("writeback_targets", []))
        role_write_scope = set(role_card.get("write_scope", []))
        role_allowed_actions = set(role_card.get("allowed_actions", []))
        workflow_still_draft = workflow_manifest.get("status") == "draft"
        promotion_record_lane_declared = (
            PROMOTION_RECORD_LANE in workflow_targets
            and PROMOTION_RECORD_LANE in role_write_scope
            and "write_promotion_record" in role_allowed_actions
        )
    if not promotion_record_lane_declared:
        blocking_issues.append(
            "Promotion-Records lane is not yet declared in the Hermes promotion workflow/role-card contract."
        )

    if workflow_still_draft:
        blocking_issues.append(
            "Hermes promotion workflow remains status='draft', so activation readiness must stay blocked."
        )

    if adapter_still_fail_closed:
        blocking_issues.append(
            "Hermes adapter manifest still keeps may_promote_to_knowledge='no' and gate_conditions_required=false, so activation readiness remains intentionally fail-closed."
        )

    ready = (
        adapter_posture_ok
        and provenance_gate_seam_present
        and control_plane_linkage_declared
        and direct_authority_guard_declared
        and promotion_record_lane_seeded
        and promotion_record_lane_declared
        and not workflow_still_draft
        and not adapter_still_fail_closed
    )

    return {
        "ready": ready,
        "adapter_posture_ok": adapter_posture_ok,
        "adapter_still_fail_closed": adapter_still_fail_closed,
        "provenance_gate_seam_present": provenance_gate_seam_present,
        "control_plane_linkage_declared": control_plane_linkage_declared,
        "direct_authority_guard_declared": direct_authority_guard_declared,
        "promotion_record_lane_seeded": promotion_record_lane_seeded,
        "promotion_record_lane_declared": promotion_record_lane_declared,
        "workflow_still_draft": workflow_still_draft,
        "blocking_issues": blocking_issues,
    }
