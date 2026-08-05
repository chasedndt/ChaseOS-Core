# Autonomous Operator Runtime

The Autonomous Operator Runtime (AOR) is ChaseOS's bounded execution and orchestration substrate. Its scope is broader than "run an AI agent": AOR coordinates explicit human, deterministic rules/code, traditional ML, and generative-AI steps while preserving one authority model, approval boundary, and evidence trail.

AOR does not make every task autonomous. It determines whether a proposed route is safe and appropriate before any runtime or provider is selected.

## Hybrid-Intelligence Boundary

AOR recognizes four step modalities:

| Modality | Use when | Authority boundary |
|---|---|---|
| `human` | Judgment, ambiguity, ethics, liability, protected decisions, or accountability dominate | The named accountable human owns the decision; timeout or denial blocks |
| `rules` | Logic is explicit, stable, exact, or security/authority sensitive | Deterministic code calculates and enforces the result |
| `ml` | Versioned statistical prediction over structured historical data is justified | Advisory unless a separate policy grants bounded action; evaluation and drift evidence are required |
| `genai` | Unstructured interpretation, synthesis, language, or flexible planning is required | Bounded nondeterminism, cost ceiling, verifier, and existing permission scope are mandatory |

Most material workflows may be `hybrid`, but every material step must declare one concrete modality. `hybrid` describes the graph; it is not an executable step type.

## Decision Modality Router

The Decision Modality Router is the deterministic AOR pre-dispatch layer. It answers:

> Should this step be handled by a human, rules/code, ML, or generative AI?

That happens before runtime routing, which answers:

> Which already-authorized runtime or tool can execute the declared step?

A model may propose or explain a route. It cannot validate its own authority. The deterministic router must accept or block the contract.

### Hard routing rules

1. Security, identity, access control, approval matching, exact arithmetic, schemas, money movement, canonical transitions, immutable state transitions, and external publication require deterministic rules/code.
2. High-stakes, critical, legal, ethical, protected, destructive, credential-bearing, money-moving, canonical, or public actions require an accountable human checkpoint.
3. ML steps require a model version, evaluation reference, and drift status.
4. Generative-AI steps require explicit bounded nondeterminism, a non-negative cost ceiling, and a verifier.
5. Missing, contradictory, or unsupported contracts fail closed.
6. Modality selection cannot expand permissions, credentials, write scope, or runtime authority.

## Approval-System Upgrade

AOR approvals become **decision-scoped checkpoints generated from the route**, rather than a vague approval for an entire AI run.

Each approval plan must identify:

- the decision and exact steps being approved;
- the accountable human;
- why approval is required;
- evidence the reviewer must inspect;
- the target/action scope;
- timeout and denial behavior;
- whether the decision is reusable.

The safe defaults are:

- `on_timeout: block`;
- `on_denial: block`;
- `reusable: false`.

Approval authorizes only the bound decision and evidence set. It does not grant broad future authority, change the Permission Matrix, select credentials, or approve unrelated steps.

## Runtime Pipeline

The intended AOR pipeline is:

1. load declared operator intent and context;
2. validate the decision contract;
3. inspect the modality route and derive approval requirements;
4. look up the registered workflow and task type;
5. resolve the role card and permission ceiling;
6. verify declared reads, inputs, and server/runtime capabilities;
7. stop at any required human checkpoint;
8. dispatch only the accepted, already-authorized step handler;
9. deterministically verify step outputs;
10. write only to declared targets through Gate rules;
11. emit route, approval, execution, and Outcome Proof evidence;
12. escalate on any scope, evidence, authority, or verification failure.

The current AOR engine already performs registry, task, role, permission, required-read, approval, handler, writeback, and audit stages. Decision-route inspection is the first read-only foothold and is not yet wired as an automatic live dispatch stage.

## Implemented Core Foothold

Core currently provides:

- `runtime/decision_router/decision_contract.schema.json`;
- deterministic `inspect_decision_contract(...)` validation;
- a read-only CLI:

```bash
chaseos decision-route inspect docs/runtime/Decision-Contract.example.json --json
```

The inspector can allow or block a proposed route and produce a decision-scoped approval plan. It explicitly reports that it cannot dispatch work, consume approval, change permissions, access credentials, or write canonical state.

## Next Integration Pass

The next reviewed pass may bind the accepted decision-route result into AOR preflight and approval packets. That pass must prove:

1. route inspection occurs before runtime/provider selection;
2. a blocked route cannot reach a handler;
3. approval identity and evidence are bound to the exact decision contract;
4. approval is single-use unless policy explicitly says otherwise;
5. provider/runtime changes cannot alter authority;
6. every material step records modality-specific verification evidence.

Until those proofs exist, the decision router remains inspection-only.

## Non-Goals

- no agent-first default;
- no LLM-calculated permissions, approvals, identity, arithmetic, or canonical transitions;
- no silent canonical promotion;
- no uncontrolled filesystem traversal;
- no credential value storage;
- no external side effects without a scoped approval;
- no automatic ML authority from a prediction;
- no private runtime state in public Core.

## Core Boundary

Core ships the generic schemas, deterministic inspection, safe CLI, pipeline pattern, and deny-by-default interfaces. Private deployments own live workflows, schedules, operator identities, approval stores, credentials, runtime state, and any proprietary enforcement kernel.

*Graph links: [[Agent-Control-Plane]] · [[ChaseOS-Gate]] · [[Permission-Matrix]] · [[Runtime-InterAgent-Coordination-Bus]] · [[OpenClaw-Runtime-Profile]]*
