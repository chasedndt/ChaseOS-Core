from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


MODALITIES = ("human", "rules", "ml", "genai")
STAKES = ("low", "medium", "high", "critical")
CANONICAL_WRITEBACK = ("none", "proposal_only", "promote")

RULES_REQUIRED_ACTIONS = {
    "access_control",
    "approval_matching",
    "canonical_transition",
    "credential_access",
    "exact_calculation",
    "identity",
    "immutable_state_transition",
    "money_movement",
    "public_publish",
    "schema_validation",
    "security",
}

HUMAN_APPROVAL_ACTIONS = {
    "canonical_transition",
    "credential_access",
    "destructive_action",
    "ethical_decision",
    "legal_decision",
    "money_movement",
    "protected_change",
    "public_publish",
}


class DecisionContractError(ValueError):
    """Raised when a decision contract is structurally invalid."""


def load_decision_contract(path: str | Path) -> dict[str, Any]:
    contract_path = Path(path)
    try:
        payload = json.loads(contract_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise DecisionContractError(f"could not read decision contract: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise DecisionContractError(f"invalid decision contract JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise DecisionContractError("decision contract must contain a JSON object")
    return payload


def _required_text(value: Any, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise DecisionContractError(f"{field_name} is required")
    return normalized


def _normalize_contract(contract: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(contract, Mapping):
        raise DecisionContractError("decision contract must be an object")
    if contract.get("schema_version") != 1:
        raise DecisionContractError("schema_version must be 1")

    decision_id = _required_text(contract.get("decision_id"), "decision_id")
    stakes = str(contract.get("stakes") or "").strip().lower()
    if stakes not in STAKES:
        raise DecisionContractError(f"stakes must be one of: {', '.join(STAKES)}")
    canonical_writeback = str(contract.get("canonical_writeback") or "").strip().lower()
    if canonical_writeback not in CANONICAL_WRITEBACK:
        raise DecisionContractError(
            f"canonical_writeback must be one of: {', '.join(CANONICAL_WRITEBACK)}"
        )

    route = contract.get("route")
    if not isinstance(route, list) or not route:
        raise DecisionContractError("route must be a non-empty list")

    normalized_route: list[dict[str, Any]] = []
    seen_steps: set[str] = set()
    for index, raw_step in enumerate(route):
        if not isinstance(raw_step, Mapping):
            raise DecisionContractError(f"route[{index}] must be an object")
        step_id = _required_text(raw_step.get("step"), f"route[{index}].step")
        if step_id in seen_steps:
            raise DecisionContractError(f"duplicate route step: {step_id}")
        seen_steps.add(step_id)
        modality = str(raw_step.get("modality") or "").strip().lower()
        if modality not in MODALITIES:
            raise DecisionContractError(
                f"unsupported modality for route step {step_id!r}: {modality!r}"
            )
        action_classes = raw_step.get("action_classes")
        if not isinstance(action_classes, list):
            raise DecisionContractError(
                f"route step {step_id!r} action_classes must be a list"
            )
        normalized_actions = sorted(
            {_required_text(value, f"route step {step_id!r} action class") for value in action_classes}
        )
        normalized_step = dict(raw_step)
        normalized_step.update(
            {"step": step_id, "modality": modality, "action_classes": normalized_actions}
        )
        normalized_route.append(normalized_step)

    accountable_human = str(contract.get("accountable_human") or "").strip() or None
    return {
        **dict(contract),
        "decision_id": decision_id,
        "stakes": stakes,
        "canonical_writeback": canonical_writeback,
        "accountable_human": accountable_human,
        "route": normalized_route,
    }


def _add_violation(
    violations: list[dict[str, str]], code: str, message: str, step: str | None = None
) -> None:
    item = {"code": code, "message": message}
    if step:
        item["step"] = step
    if item not in violations:
        violations.append(item)


def inspect_decision_contract(contract: Mapping[str, Any]) -> dict[str, Any]:
    """Inspect a route deterministically; never dispatch, approve, or mutate authority."""

    normalized = _normalize_contract(contract)
    route = normalized["route"]
    violations: list[dict[str, str]] = []
    modalities: list[str] = []
    approval_reasons: set[str] = set()
    approval_scope: set[str] = set()
    evidence_required: set[str] = set()

    if normalized["stakes"] in {"high", "critical"}:
        approval_reasons.add(f"{normalized['stakes']}_stakes")

    if normalized["canonical_writeback"] == "promote":
        approval_reasons.add("canonical_writeback")

    rules_actions = {
        action
        for step in route
        if step["modality"] == "rules"
        for action in step["action_classes"]
    }

    for step in route:
        step_id = step["step"]
        modality = step["modality"]
        actions = set(step["action_classes"])
        if modality not in modalities:
            modalities.append(modality)

        for action in sorted(actions & RULES_REQUIRED_ACTIONS):
            if action not in rules_actions:
                _add_violation(
                    violations,
                    f"{action}_requires_rules",
                    f"action class {action!r} must include a deterministic rules/code step",
                    step_id,
                )

        approval_actions = actions & HUMAN_APPROVAL_ACTIONS
        if approval_actions:
            approval_reasons.update(approval_actions)
            approval_scope.add(step_id)
        if modality == "human":
            approval_scope.add(step_id)

        verifier = str(step.get("verifier") or "").strip()
        if not verifier:
            _add_violation(
                violations,
                "verifier_required",
                "every material route step must name a verifier",
                step_id,
            )
        elif approval_actions or modality == "human":
            evidence_required.add(verifier)

        if modality == "genai":
            if step.get("nondeterminism_allowed") is not True:
                _add_violation(
                    violations,
                    "genai_nondeterminism_contract_required",
                    "genai steps must explicitly allow bounded nondeterminism",
                    step_id,
                )
            ceiling = step.get("cost_ceiling_usd")
            if isinstance(ceiling, bool) or not isinstance(ceiling, (int, float)) or ceiling < 0:
                _add_violation(
                    violations,
                    "genai_cost_ceiling_required",
                    "genai steps must declare a non-negative cost_ceiling_usd",
                    step_id,
                )

        if modality == "ml":
            for field_name, code in (
                ("model_version", "ml_model_version_required"),
                ("evaluation_ref", "ml_evaluation_required"),
                ("drift_status", "ml_drift_status_required"),
            ):
                if not str(step.get(field_name) or "").strip():
                    _add_violation(
                        violations,
                        code,
                        f"ml steps must declare {field_name}",
                        step_id,
                    )
            if step.get("drift_status") == "blocked":
                _add_violation(
                    violations,
                    "ml_drift_blocked",
                    "ml step cannot run while drift_status is blocked",
                    step_id,
                )

    approval_required = bool(approval_reasons)
    has_human_checkpoint = "human" in modalities
    if approval_required and not normalized["accountable_human"]:
        _add_violation(
            violations,
            "accountable_human_required",
            "approval-required decisions must name an accountable_human",
        )
    if approval_required and not has_human_checkpoint:
        _add_violation(
            violations,
            "human_checkpoint_required",
            "approval-required decisions must contain an explicit human route step",
        )

    approval_plan = {
        "required": approval_required,
        "accountable_human": normalized["accountable_human"] if approval_required else None,
        "decision_id": normalized["decision_id"],
        "decision_scope": sorted(approval_scope) if approval_required else [],
        "reasons": sorted(
            approval_reasons,
            key=lambda value: (
                0 if value in {"high_stakes", "critical_stakes"} else 1,
                value,
            ),
        ),
        "evidence_required": sorted(evidence_required) if approval_required else [],
        "on_timeout": "block",
        "on_denial": "block",
        "reusable": False,
    }

    if not approval_required:
        approval_plan = {
            "required": False,
            "accountable_human": None,
            "decision_id": normalized["decision_id"],
            "decision_scope": [],
            "reasons": [],
            "evidence_required": [],
            "on_timeout": "block",
            "on_denial": "block",
            "reusable": False,
        }

    return {
        "ok": not violations,
        "status": "allowed" if not violations else "blocked",
        "decision_id": normalized["decision_id"],
        "required_modalities": modalities,
        "approval_plan": approval_plan,
        "violations": violations,
        "violation_codes": [item["code"] for item in violations],
        "route": route,
        "authority": {
            "inspection_only": True,
            "dispatch_allowed": False,
            "approval_consumption_allowed": False,
            "permission_change_allowed": False,
            "canonical_writeback_allowed": False,
            "credential_access_allowed": False,
        },
    }
