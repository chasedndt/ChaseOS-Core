from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from runtime.decision_router import DecisionContractError, inspect_decision_contract


RULES_CONTRACT = {
    "schema_version": 1,
    "decision_id": "monthly-balance",
    "stakes": "low",
    "accountable_human": None,
    "canonical_writeback": "none",
    "route": [
        {
            "step": "calculate_balance",
            "modality": "rules",
            "action_classes": ["exact_calculation"],
            "verifier": "unit-tests",
        }
    ],
}


def test_exact_calculation_routes_to_rules_without_approval() -> None:
    result = inspect_decision_contract(RULES_CONTRACT)

    assert result["ok"] is True
    assert result["status"] == "allowed"
    assert result["required_modalities"] == ["rules"]
    assert result["approval_plan"]["required"] is False
    assert result["authority"]["dispatch_allowed"] is False
    assert result["authority"]["permission_change_allowed"] is False


def test_money_movement_cannot_be_assigned_to_genai() -> None:
    contract = {
        **RULES_CONTRACT,
        "decision_id": "send-payment",
        "stakes": "high",
        "accountable_human": "finance-owner",
        "route": [
            {
                "step": "transfer",
                "modality": "genai",
                "action_classes": ["money_movement"],
                "verifier": "receipt-readback",
            }
        ],
    }

    result = inspect_decision_contract(contract)

    assert result["ok"] is False
    assert result["status"] == "blocked"
    assert "money_movement_requires_rules" in result["violation_codes"]
    assert result["approval_plan"]["required"] is True


def test_high_stakes_route_builds_decision_scoped_approval_plan() -> None:
    contract = {
        **RULES_CONTRACT,
        "decision_id": "publish-release",
        "stakes": "high",
        "accountable_human": "release-owner",
        "route": [
            {
                "step": "prepare_release",
                "modality": "genai",
                "action_classes": ["unstructured_synthesis"],
                "verifier": "release-checks",
                "nondeterminism_allowed": True,
                "cost_ceiling_usd": 0.25,
            },
            {
                "step": "approve_release",
                "modality": "human",
                "action_classes": ["public_publish"],
                "verifier": "operator-decision",
            },
            {
                "step": "publish_release",
                "modality": "rules",
                "action_classes": ["public_publish"],
                "verifier": "fetch-back",
            },
        ],
    }

    result = inspect_decision_contract(contract)

    assert result["ok"] is True
    assert result["approval_plan"] == {
        "required": True,
        "accountable_human": "release-owner",
        "decision_id": "publish-release",
        "decision_scope": ["approve_release", "publish_release"],
        "reasons": ["high_stakes", "public_publish"],
        "evidence_required": ["fetch-back", "operator-decision"],
        "on_timeout": "block",
        "on_denial": "block",
        "reusable": False,
    }


def test_missing_accountable_human_fails_closed_for_canonical_writeback() -> None:
    contract = {
        **RULES_CONTRACT,
        "decision_id": "promote-note",
        "canonical_writeback": "promote",
        "route": [
            {
                "step": "promote",
                "modality": "rules",
                "action_classes": ["canonical_transition"],
                "verifier": "promotion-receipt",
            }
        ],
    }

    result = inspect_decision_contract(contract)

    assert result["ok"] is False
    assert "accountable_human_required" in result["violation_codes"]
    assert "human_checkpoint_required" in result["violation_codes"]


def test_genai_step_requires_bounded_nondeterminism_cost_and_verifier() -> None:
    contract = {
        **RULES_CONTRACT,
        "decision_id": "draft-brief",
        "route": [
            {
                "step": "draft",
                "modality": "genai",
                "action_classes": ["unstructured_synthesis"],
            }
        ],
    }

    result = inspect_decision_contract(contract)

    assert result["ok"] is False
    assert {
        "genai_nondeterminism_contract_required",
        "genai_cost_ceiling_required",
        "verifier_required",
    }.issubset(set(result["violation_codes"]))


def test_missing_or_unknown_contract_fields_raise_clear_contract_error() -> None:
    with pytest.raises(DecisionContractError, match="route must be a non-empty list"):
        inspect_decision_contract(
            {
                "schema_version": 1,
                "decision_id": "broken",
                "stakes": "low",
                "canonical_writeback": "none",
            }
        )

    bad = {**RULES_CONTRACT, "route": [{**RULES_CONTRACT["route"][0], "modality": "magic"}]}
    with pytest.raises(DecisionContractError, match="unsupported modality"):
        inspect_decision_contract(bad)


def test_core_cli_inspects_contract_without_dispatching(tmp_path: Path) -> None:
    contract_path = tmp_path / "decision.json"
    contract_path.write_text(json.dumps(RULES_CONTRACT), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "runtime.cli.core_main",
            "decision-route",
            "inspect",
            str(contract_path),
            "--json",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["action"] == "decision-route.inspect"
    assert payload["result"]["status"] == "allowed"
    assert payload["result"]["authority"]["dispatch_allowed"] is False
