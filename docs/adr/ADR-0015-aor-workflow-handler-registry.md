# ADR-0015 — AOR engine ↔ workflow-handler decoupling (tier-classified registry)

- **Status:** Accepted
- **Date:** 2026-06-21

## Context

`runtime/aor/` is the bounded-execution engine and the keystone of MIT Core. Its authority
checks were already abstracted behind the Core port from
[ADR-0014](ADR-0014-core-gate-interface.md), so the engine had no proprietary imports.

The remaining blocker was **workflow dispatch**. The engine resolved handlers through a
27-branch `if`-chain that lazily hard-imported each concrete workflow handler by name.
Several of those handlers are instance-specific — they belong to private business or
personal deployments and must never ship in a public framework.

Even though the imports were lazy, the engine *module* still enumerated the full instance
workflow set by name. Extracting it as-is would either drag private modules along or ship an
engine referencing modules that do not exist in Core. Both are unacceptable: the first leaks
private surface, the second ships a broken reference.

## Decision

Invert the engine → workflow dependency with a **workflow handler registry**, reusing the
dependency-inversion pattern from ADR-0014.

`runtime/aor/workflow_handlers.py` maps `workflow_id → (lazy loader, tier)`. The engine
resolves handlers **only** through the registry and never names a concrete workflow.

Handlers are classified by tier, and the tier declares the boundary as data rather than
burying it in control flow:

| Tier | Character | Ships in Core |
|---|---|---|
| `core` | Generic framework workflows | Yes |
| `runtime` | Generic coordination over third-party runtimes | Yes |
| `shadow` | Research/development shadows | Yes (dev) |
| `instance` | Private personal or business deployments | **No** |

Core ships the engine, the registry, and the `core`/`runtime`/`shadow` registrations.
Instance-tier workflows register themselves externally, from a registration module loaded
only by a private deployment's bootstrap — never imported by the engine. In a Core-only
configuration an unknown `workflow_id` resolves to `None`, and the engine escalates rather
than crashing.

A publish-readiness test asserts that Core can import the engine and resolve a core-tier
workflow with no instance module present, and that an instance-tier id resolves to `None` in
that configuration.

## Consequences

- The engine is Core-extractable without dragging private workflows into a public repo.
- New workflows register instead of editing the engine, so the branch chain stops growing
  and the Core/instance boundary becomes declarative.
- Resolution gains one level of indirection (registry lookup rather than an inline branch).
  Accepted — lookup stays lazy, so import cost is unchanged.
- A missing registration now fails as `None` → escalation rather than an `ImportError`.
  This is safer, but it means a typo'd `workflow_id` surfaces as an escalation rather than a
  loud import failure; the readiness test exists to catch that class of mistake.

## Open items

- Whether the `runtime` tier ships in Core or in a thin adjacent package, depending on the
  final licensing placement of the third-party runtime adapters.
- Entry-point-based registration, as an alternative to an explicit bootstrap import, can
  come later; the explicit bootstrap is sufficient for the current split.
