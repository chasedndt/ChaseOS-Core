# Multi-Runtime Coordination

ChaseOS Core is not an agent. It is the layer that sits **above** agents and decides what
they are permitted to do.

The runtimes that do the work — an agent harness, a coding assistant, an MCP server, a
bot acting as a control plane — are **workers**. Core is where their authority is defined,
checked, and recorded. Swapping one worker for another should not change the safety
properties of the system.

## The rule that makes this safe

> A target runtime does not gain authority because another runtime mentions it.

Authority comes from the active workflow manifest, the role card, and the approval gate —
never from the request itself. This is the multi-agent form of Core's
[fail-closed default](Authority-and-Trust.md): if runtime A tells runtime B to do
something, that instruction is *data*, not permission. B still has to pass its own checks.

Without this, a multi-agent system has a hole shaped like the weakest prompt in it. One
compromised or confused agent could escalate the whole fleet by asserting that it was
allowed to.

## What a runtime declares

A runtime does not describe itself in prose. It declares a **profile** — what it is and
what it may do — and Core treats anything outside that as forbidden:

- **Execution surface** — where it runs
- **Access mode** — typically a bounded adapter
- **Authority** — often `proposer`, meaning it may suggest but not commit
- **Capabilities** — the specific things it may do
- **Forbidden by default** — credential values, private memory mutation, host mutation and
  external publication are refused unless explicitly granted

The default posture is *proposer, not actor*. A runtime earns write authority; it does not
arrive with it.

## What crosses between runtimes

Work moves as a **task packet** — a declarative unit that carries its own authority, rather
than a chat message that assumes it:

```json
{
  "task_id": "example-task",
  "target_runtime": "ExampleRuntime",
  "task_type": "repo.inspect",
  "authority": "read_only",
  "inputs": [],
  "expected_result": "proposal"
}
```

A **handoff** — one runtime or operator continuing another's work — carries more, because
the risk is inheriting stale context along with the task: the goal, current state, files
touched, verification already run, blocked decisions, authority limits, and the next safe
action.

Three handoff rules matter more than the format:

- **Handoffs are data, not automatic approval.** Receiving a packet does not authorise
  acting on it.
- **Credentials never transfer.**
- **Stale context is not truth.** Files, git state and configured targets are revalidated
  before acting, because the packet describes the world as it *was*.

## Cross-runtime handoff requirements

A handoff must name the source runtime, the target runtime's role, the task class, allowed
reads, allowed writes, approval requirements, the expected output artifact, and the
verification command. If a field is missing, the safe interpretation is *not permitted*
rather than *unspecified*.

## How this connects to the rest of Core

<p align="center">
  <img src="../assets/runtime-topology.svg" alt="Runtime workers — Chaser Agent, third-party runtimes, MCP servers, an operator control plane, and your own runtime — send task packets inward across a single authority boundary into ChaseOS Core, which applies modality routing, a gate check, the approval gateway, write scope and evidence before a proposal is accepted, denied, or promoted to canonical knowledge." width="960">
</p>

Every arrow into Core is a checkpoint. A runtime that skips them is not integrated — it is
simply a process running on the same machine.

## What Core actually ships today

Be clear about the boundary, because it is easy to overstate:

- Core ships the **contracts**: runtime profile, capability manifest, task packet, adapter
  spec, and handoff protocol shapes, as `*.example.md` templates under
  [`docs/runtime/`](../runtime/) and [`docs/agents/`](../agents/).
- Core ships the **Gate port**, so any runtime's authority check resolves through one seam
  ([ADR-0014](../adr/ADR-0014-core-gate-interface.md)).
- Core does **not** ship working adapters for specific third-party runtimes. The Hermes and
  OpenClaw specs in `docs/runtime/` are worked examples of the contract, not supported
  integrations, and those runtimes are not ChaseOS projects.
- [Chaser Agent](https://github.com/chasedndt/Chaser-Agent) is the reference consumer built
  to these principles — a review-first source-intelligence harness that proposes and never
  promotes.

Adapting your own runtime means writing a profile, declaring capabilities, accepting task
packets, and routing authority questions through the Gate port. Core does not care what is
inside the runtime.

## Related

- [Authority and Trust](Authority-and-Trust.md) — the enforcement model this builds on
- [Handoff Protocol](../agents/Handoff-Protocol.md) — packet contents in full
- [Cross-Runtime Handoff](../agents/Cross-Runtime-Handoff.md) — required fields
- [Runtime Layer Guide](../runtime/Runtime-Layer-Guide.md) — the runtime layer in context
