# Approval Center

The Approval Center is the review surface for proposed decisions and actions that require accountable operator or policy approval.

Approval is not a generic permission boost. It is a decision-scoped, evidence-bound checkpoint in a bounded AOR route.

## What belongs here

- high-stakes or critical decisions;
- protected or canonical changes;
- write-capable workflow steps;
- publication and external egress;
- connector activation;
- credential-bound operations;
- money movement or commercial commitments;
- destructive or difficult-to-reverse actions;
- actions whose declared modality route requires accountable human judgment.

Low-risk, reversible, deterministic inspection should not be interrupted by unnecessary approval prompts. The decision route should escalate only the exact steps that cross an approval boundary.

## Approval record

An approval record should include:

- `approval_id` and bound `decision_id`;
- requester, runtime, workflow, and accountable human identity;
- exact decision/action and step scope;
- target, allowed writes, origins, and permission ceiling;
- reason approval is required;
- evidence and verifier output the reviewer must inspect;
- payload/artifact identity or hashes where applicable;
- expiration and timeout behavior;
- denial behavior;
- replay/reuse policy;
- response and execution linkage;
- post-action verification requirements.

## Safe defaults

- timeout blocks;
- denial blocks;
- approvals are single-use;
- approval for one step does not authorize another;
- approval cannot expand the Permission Matrix or runtime ceiling;
- changed payload, target, route, or evidence invalidates the approval;
- approval consumption and execution remain deterministic;
- external effects require post-action verification and a receipt.

## Relationship to decision routing

The deterministic Decision Modality Router derives an approval plan from a proposed human/rules/ML/genAI route. The plan explains which steps require a human and why. A model may draft an explanation, but it cannot create, approve, match, or consume its own authority.

Core's current decision-route command is inspection-only. It does not queue or consume approvals. Live approval storage and enforcement belong to a configured deployment Gate/ApprovalGateway and must fail closed when that backend is unavailable.
