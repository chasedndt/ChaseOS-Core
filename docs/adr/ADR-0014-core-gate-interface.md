# ADR-0014 — Core Gate interface (dependency inversion) for the Control Kernel

- **Status:** Accepted
- **Date:** 2026-06-21

## Context

Several Core-eligible runtime modules needed authority checks from the **Control Kernel** —
the policy-driven enforcement engine that decides whether a runtime operation is permitted.
Those modules imported the Control Kernel (`runtime.chaseos_gate`) at module scope.

That is a hard dependency from open framework code onto a proprietary component, and it
blocks MIT extraction outright: **Core must never depend on a proprietary module.** The
dependency was also heavier than necessary — the consuming modules used only four
operations, all returning simple types, and the decisive one (`check_runtime_operation`) is
deny-by-default.

## Decision

Invert the dependency with a **port plus registered implementation**.

`runtime/gate_interface.py` defines a `GateProvider` Protocol covering the four operations,
with a module-level delegating API that mirrors the Control Kernel's signatures. Core
modules import gate operations from `runtime.gate_interface` and **never** from the Control
Kernel directly.

Provider resolution runs in a fixed order:

1. an explicitly registered provider via `register_gate(provider)`;
2. auto-wire to the Control Kernel when it is installed (the full proprietary deployment);
3. a **deny-by-default fallback** when neither is present.

The optional Control Kernel import inside the port is lazy and guarded, so
`gate_interface` stays import-clean for Core — there is no top-level proprietary import.

### The open/proprietary line

The generic gate *mechanism* and this *port* are open: an inspectable trust surface is the
point of the project, and it is what lets someone audit where authority is checked. The
*enforcement product* stays proprietary — the Control Kernel was deliberately not moved into
Core. It remains the implementation carrying managed policy, entitlement enforcement, and
tamper-evident approval records.

A pure MIT Core instance therefore runs the deny-by-default fallback. That is safe by
construction and is the intended open-core boundary: Core can be inspected, forked, and
extended, and fully gated operation is supplied by binding a real provider behind the port.

## Consequences

- Core modules are decoupled from the proprietary enforcement engine and can ship under MIT.
- A Core-only deployment **denies** gated operations rather than permitting them. This is
  intentional, but it means a fork must register its own `GateProvider` before gated
  operations will succeed — a fork that skips this will see denials, not silent allowance.
- The port must track the Control Kernel's signatures; drift between the two is a real
  maintenance cost and is covered by delegation-parity tests.
- Dependency inversion here established the pattern reused for workflow dispatch in
  [ADR-0015](ADR-0015-aor-workflow-handler-registry.md).

## Open items

- A Core contract-constants module for gate operation/schema identifiers, so the port covers
  constants as well as functions. Until then, modules needing those constants remain outside
  Core.
