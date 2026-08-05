# Concepts — start here

ChaseOS Core has more moving parts than you need on day one. This page is the shortest
honest path through them: **five concepts to understand the system, six more to extend
it**, and everything else deferred until you actually need it.

If you only read one thing, read *The one idea* below.

## The one idea

Most agent frameworks answer *"can the system do this?"* ChaseOS Core answers a different
question first: **"is the system allowed to do this, who is accountable, and what evidence
will exist afterward?"**

Capability is assumed. **Authority is the thing being engineered.** Every design decision in
Core follows from that — the fail-closed gate, quarantined input, promotion instead of
writeback, and the modality router all exist to make authority explicit rather than
implicit.

## The first five (understand the system)

Read in this order. Each builds on the last.

### 1. Core vs. instance

Core is the public framework. Your private identity, credentials, live runtime state, and
real notes live in a **separate private instance** built on top of it. Core deliberately
ships no secrets and no personal data, and it never gains them by being used.

→ [Core vs Instance](Core-vs-Instance.md) · [FORKING.md](../../FORKING.md)

### 2. The vault and its layers

A *vault* is the directory a ChaseOS instance operates on. Content is organised into seven
layers — from current state (Home) through projects, knowledge, raw input, SOPs, runtime,
and evidence. The numbered folders (`00_HOME/`, `01_PROJECTS/`, …) are that model on disk.

Not everything needs a vault: much of Core works as a plain library. See
[which parts need one](../../examples/README.md#which-parts-of-core-are-library-usable).

→ [Core Operating Model](Core-Operating-Model.md)

### 3. Canonical truth is promoted, never written

Agents do not write facts into your knowledge base. Captured material lands in an input or
quarantine area; it becomes canonical only by passing an explicit **promotion gate** with
provenance and a review decision. This is why an agent cannot quietly corrupt your memory.

→ [Canonical Truth and Promotion](Canonical-Truth-and-Promotion.md)

### 4. Authority, trust tiers, and fail-closed

Every gated operation is checked against a policy provider before it runs. A pure MIT Core
install has **no provider bound**, so gated operations are **denied**, not permitted. This
surprises people, and it is deliberate: an un-kerneled Core never silently allows something.

→ [Authority and Trust](Authority-and-Trust.md) · [Permission Matrix](../../kernel/PERMISSION_MATRIX.md)

### 5. Modality routing

Before asking *which model*, Core asks *which kind of actor* should own a step: a human,
deterministic code, an ML model, or a generative agent. Some action classes (money
movement, access control, canonical transitions) **cannot** be delegated to a generative
model — the router blocks routes that try.

→ [Decision Modality Routing](Decision-Modality-Routing.md)

At this point you understand the system. Everything below is for building on it.

## The next six (extend the system)

### 6. The Gate port

`runtime.gate_interface` is the seam where you supply your own authority policy. Implement
`GateProvider`, call `register_gate()`, done. This is the main extension point.

→ [ADR-0014](../adr/ADR-0014-core-gate-interface.md) · [example 02](../../examples/02_custom_approval_gateway.py)

### 7. Approval gateway and action specs

Distinct from the Gate: the `ApprovalGateway` queues an action for a human decision.
Core ships a deny-only implementation, because Core has no approval store of its own.

→ [Approval Center](../governance/Approval-Center.md)

### 8. Connections

Providers (Slack, GitHub, local files…) are described as **data** — YAML manifests
declaring each capability's action type, safety level, and whether it is on by default.
Nothing dangerous is enabled out of the box.

→ [Connections](../connections/README.md) · [example 03](../../examples/03_load_connection_manifests.py)

### 9. Workflows and tiers

The Autonomous Operator Runtime resolves workflows through a registry where each handler
carries a **tier**. Core ships generic tiers only; instance-specific workflows register
externally. Core ships no workflow manifests, so `chaseos run` escalates rather than
executing — that is expected, not a bug.

→ [ADR-0015](../adr/ADR-0015-aor-workflow-handler-registry.md)

### 10. Multi-runtime coordination

Core is not an agent — it is the layer above them. Runtimes declare a profile, propose by
default, and pass work as task packets. The governing rule: a target runtime does not gain
authority because another runtime mentions it.

→ [Multi-Runtime Coordination](Multi-Runtime-Coordination.md)

### 11. Evidence

Runs produce audit records. Evidence is what makes an approval meaningful after the fact —
without it, "approved" is just a claim.

→ [Runtime Layer Guide](../runtime/Runtime-Layer-Guide.md)

## Deliberately deferred

You do **not** need these to start, and reading them early is the main reason Core feels
heavy. Come back when you have a concrete reason:

role cards · task-type tables · OSRIL · schedules · graph substrate · source intelligence ·
capture connectors · commerce/entitlement surfaces

## Where to go next

| If you want to… | Go to |
|---|---|
| Use Core in your own project | [examples/](../../examples/) |
| See the whole architecture | [ARCHITECTURE.md](../ARCHITECTURE.md) |
| Look up a term | [Glossary](Glossary.md) |
| Run the CLI | [Quickstart](../getting-started/Quickstart.md) |
| Fork Core as your own OS | [FORKING.md](../../FORKING.md) |
