# Glossary

Every term ChaseOS Core uses in a specific way. Ordered alphabetically; if you are reading
this first, start with [Concepts](README.md) instead.

Terms marked **(Core)** are implemented in this repository. Terms marked *(instance)* refer
to things a private deployment supplies.

---

**Action class** — A label on a route step describing *what kind of thing* it does
(`schema_validation`, `money_movement`, `public_publish`). Certain action classes force a
deterministic step or a human approval regardless of what the author intended. **(Core)**

**ActionSpec** — The payload describing a gated action (type, target path, content,
submitter, note) submitted to an `ApprovalGateway`. **(Core)**

**Adapter** — Code that connects Core's contracts to a specific external runtime or
provider. Adapters are bounded by manifests and never bypass the Gate.

**AOR (Autonomous Operator Runtime)** — The bounded execution engine that runs workflows.
It resolves handlers through a registry, applies write scopes, and escalates rather than
crashing when it cannot proceed. **(Core)**

**ApprovalGateway** — The port for queuing an action for human approval and reading back
the decision. Core ships only a deny-only implementation, because Core has no approval
store. See [ADR-0014](../adr/ADR-0014-core-gate-interface.md). **(Core)**

**Approval plan** — The derived requirements for approving a decision: who is accountable,
which steps are in scope, why approval is required, what evidence is needed, and what
happens on timeout or denial. Produced by inspection *without* executing anything. **(Core)**

**Canonical knowledge** — Content that has passed the promotion gate and can be relied on.
The opposite of a draft. Agents cannot write here directly.

**Capability** — One declared ability of a connection provider (`github.create_issue`),
carrying an action type, a safety level, and an `enabled_by_default` flag. In Core, no write
or egress capability is enabled by default. **(Core)**

**Connection** — A configured link to an external provider, described by a manifest and
tracked in a local registry with status, permissions, and audit state. **(Core)**

**Control Kernel** — The proprietary authority-enforcement engine. Core defines the *port*
it plugs into but does not contain it; without it, Core denies by default. *(instance)*

**Core** — This repository: the public MIT framework layer. Contains contracts, governance,
and fail-closed adapters — never credentials, personal data, or live state.

**Decision contract** — A JSON document describing a decision: its stakes, whether it writes
canonically, who is accountable, and the ordered route of steps. Validated against
`decision_contract.schema.json`. **(Core)**

**Deny-by-default / fail-closed** — The principle that an unconfigured or unavailable
authority check results in denial, never permission. The defining safety property of Core.
**(Core)**

**Evidence** — Durable records (logs, audits, run records) that make a past decision
verifiable. An approval without evidence is only a claim.

**Escalation** — What the AOR does when it cannot safely proceed: stop and surface, rather
than guess or crash. A workflow resolving to `escalated` in Core is normal — Core ships no
workflow manifests. **(Core)**

**Gate** — The authority check applied to a runtime operation. Reached through
`runtime.gate_interface`, never by importing an enforcement engine directly. **(Core)**

**GateProvider** — The Protocol you implement to supply your own authority policy. Any
object with the right methods qualifies; no subclassing. **(Core)**

**Instance** — A private deployment built on Core, holding real credentials, live runtime
state, personal content, and deployment policy. *(instance)*

**Manifest** — A declarative description (usually YAML) of what something is permitted to
do. Used for connection providers and runtime adapters. Behaviour is data, not code.
**(Core)**

**Modality** — The *kind of actor* responsible for a step: `human`, `rules` (deterministic
code), `ml`, or `genai`. Chosen before any provider or model. **(Core)**

**Modality router** — The component that validates a decision contract, selects modalities,
and derives the approval plan. Strictly inspection-only: it never dispatches, approves, or
writes. **(Core)**

**Promotion / promotion gate** — The explicit review step by which provisional material
becomes canonical knowledge. Requires provenance, a decision, and a reason. **(Core)**

**Provenance** — Where a piece of content came from and how it was produced. Required before
promotion; enforced by `check_provenance_minimums`. **(Core)**

**Quarantine** — Where untrusted captured input sits before review. Nothing reaches
canonical knowledge without leaving quarantine deliberately. **(Core)**

**Role card** — A declarative description of what a given runtime role may do. Part of the
bounded-execution model. *(mostly instance)*

**Route / route step** — The ordered list of steps in a decision contract. Each step names a
modality, its action classes, and a verifier. **(Core)**

**Safety level** — A capability's risk classification (`safe`, `sensitive`,
`approval_required`), used to decide gating. **(Core)**

**Stakes** — A decision's risk band (`low`, `medium`, `high`, `critical`). High and critical
stakes force approval. **(Core)**

**Tier (trust tier)** — The authority ceiling assigned to a runtime or adapter — how much it
is permitted to do at most. See [Permission Matrix](../../kernel/PERMISSION_MATRIX.md).

**Tier (workflow)** — A workflow handler's classification (`core`, `runtime`, `shadow`,
`instance`) determining whether it ships publicly. See
[ADR-0015](../adr/ADR-0015-aor-workflow-handler-registry.md). **(Core)**

**Vault / vault root** — The directory a ChaseOS instance operates on, identified by markers
such as `00_HOME/` or `.chaseos/`. Many Core APIs need no vault at all. **(Core)**

**Verifier** — Who or what confirms a route step was performed correctly. Every material
step must name one, or the route is blocked. **(Core)**

**Write scope** — The bounded set of paths an execution is permitted to modify. Execution
outside scope is a violation, not a warning.

---

Two terms are easy to confuse:

- **Gate** vs **ApprovalGateway** — the Gate answers *"is this permitted by policy?"*
  synchronously. The ApprovalGateway asks *"will a human approve this?"* and involves a
  queue and a record. Both are ports; both fail closed in Core.
- **Trust tier** vs **workflow tier** — unrelated. The first is an authority ceiling; the
  second is a publication classification.
