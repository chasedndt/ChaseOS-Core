"""Inspect a decision contract without executing it.

The Decision Modality Router answers a question that comes *before* "which model should
run this?" — namely "should a human, deterministic code, an ML model, or a generative
agent be responsible for this step at all?"

Inspection is read-only by construction: it never dispatches the route, consumes an
approval, changes a permission, or writes anything. It returns the route plus the approval
plan the route would require.

Run:  python examples/01_inspect_decision_route.py
"""

from runtime.decision_router.router import inspect_decision_contract

# A contract for publishing a public changelog entry. Publishing is a "public_publish"
# action class, which Core treats as both rules-requiring and approval-requiring.
CONTRACT = {
    "schema_version": 1,
    "decision_id": "publish-release-notes",
    "stakes": "high",
    "canonical_writeback": "promote",
    "accountable_human": "release-owner",
    "route": [
        {
            "step": "draft",
            "modality": "genai",
            "action_classes": ["summarize"],
            "verifier": "release-owner",
            # genai steps must explicitly opt into bounded nondeterminism and declare a
            # cost ceiling, otherwise the route is blocked.
            "nondeterminism_allowed": True,
            "cost_ceiling_usd": 0.50,
        },
        {
            "step": "validate-schema",
            "modality": "rules",
            "action_classes": ["schema_validation", "public_publish"],
            "verifier": "ci-pipeline",
        },
        {
            "step": "approve",
            "modality": "human",
            "action_classes": ["public_publish"],
            "verifier": "release-owner",
        },
    ],
}


def main() -> None:
    result = inspect_decision_contract(CONTRACT)

    print(f"decision      : {result['decision_id']}")
    print(f"status        : {result['status']}")
    print(f"modalities    : {', '.join(result['required_modalities'])}")

    plan = result["approval_plan"]
    print("\napproval plan")
    print(f"  required          : {plan['required']}")
    print(f"  accountable human : {plan['accountable_human']}")
    print(f"  scope             : {', '.join(plan['decision_scope'])}")
    print(f"  reasons           : {', '.join(plan['reasons'])}")
    print(f"  evidence required : {', '.join(plan['evidence_required'])}")
    print(f"  on timeout/denial : {plan['on_timeout']} / {plan['on_denial']}")

    if result["violations"]:
        print("\nviolations")
        for violation in result["violations"]:
            step = violation.get("step", "-")
            print(f"  [{violation['code']}] {step}: {violation['message']}")

    # Inspection never grants authority. These are always False.
    authority = result["authority"]
    print(f"\ninspection only   : {authority['inspection_only']}")
    print(f"dispatch allowed  : {authority['dispatch_allowed']}")

    assert result["ok"], "expected this contract to pass inspection"
    assert plan["required"], "high-stakes public publish must require approval"
    assert authority["dispatch_allowed"] is False, "inspection must never allow dispatch"

    # Now show the router *catching* a bad route: a public_publish action with no
    # deterministic rules step and no human checkpoint.
    unsafe = {
        "schema_version": 1,
        "decision_id": "publish-unreviewed",
        "stakes": "high",
        "canonical_writeback": "promote",
        "accountable_human": "release-owner",
        "route": [
            {
                "step": "auto-publish",
                "modality": "genai",
                "action_classes": ["public_publish"],
                "verifier": "release-owner",
                "nondeterminism_allowed": True,
                "cost_ceiling_usd": 0.10,
            }
        ],
    }
    blocked = inspect_decision_contract(unsafe)
    print(f"\nunsafe route status : {blocked['status']}")
    for code in blocked["violation_codes"]:
        print(f"  blocked by        : {code}")

    assert blocked["status"] == "blocked", "unsafe route should be blocked"
    print("\nOK")


if __name__ == "__main__":
    main()
