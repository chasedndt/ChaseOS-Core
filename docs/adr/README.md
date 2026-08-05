# Architecture Decision Records

An **Architecture Decision Record (ADR)** is a short document capturing one significant
architectural decision: the context that forced the choice, the decision itself, and the
consequences accepted as a result. ADRs are immutable once accepted — a decision that
changes later gets a *new* ADR that supersedes the old one, rather than an edit. The point
is that a reader (or a future maintainer) can reconstruct *why* the system looks the way it
does, not just *what* it does.

ChaseOS Core references ADR numbers directly in source comments and docstrings — for
example `runtime/gate_interface.py` cites ADR-0014, and `runtime/aor/workflow_handlers.py`
cites ADR-0015. This directory publishes the records behind the decisions that are visible
in Core's shipped code.

## Published records

| ADR | Title | Status |
|---|---|---|
| [ADR-0014](ADR-0014-core-gate-interface.md) | Core Gate interface (dependency inversion) | Accepted |
| [ADR-0015](ADR-0015-aor-workflow-handler-registry.md) | AOR engine ↔ workflow-handler decoupling | Accepted |

## Why the numbering has gaps

ChaseOS maintains a single ADR sequence across the whole product, including surfaces that
are **not** part of MIT Core — commercial catalogue, entitlement and billing abstractions,
identity/workspace modelling, and marketplace decisions. Those records govern proprietary
components and are not published here, per the publication standard in
[`CORE_MANIFEST.md`](../../CORE_MANIFEST.md).

Only ADRs whose decisions are observable in this repository's code are published, so the
numbering is intentionally sparse. Where Core code cites an unpublished ADR, the relevant
contract is documented in the module docstring itself.

## Format

New records follow [`TEMPLATE.md`](TEMPLATE.md): Status, Date, Context, Decision,
Consequences, and Open items. Keep them short — an ADR is a decision record, not a design
manual. Deeper design detail belongs in [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md) or the
relevant subsystem doc.
