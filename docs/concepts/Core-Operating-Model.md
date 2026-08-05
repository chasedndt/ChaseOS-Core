# Core Operating Model

ChaseOS Core is a framework for operating a local-first human-AI system. It defines how memory, projects, knowledge, runtime agents, approvals, and evidence fit together.

## Canonical Layers

1. **Home layer** — current operating state, principles, dashboard, and active priorities.
2. **Project layer** — active work organized as project operating systems.
3. **Knowledge layer** — reusable source notes, synthesis notes, and promoted knowledge.
4. **Input layer** — raw captures and quarantine before trust or promotion.
5. **SOP/template layer** — repeatable work patterns and file shapes.
6. **Agent/runtime layer** — bounded runtimes, permissions, adapters, and handoff contracts.
7. **Evidence layer** — logs, build records, audits, and verifiable outputs.

On disk these are the numbered directories — `00_HOME/`, `01_PROJECTS/`, `02_KNOWLEDGE/`,
`03_INPUTS/`, `04_SOPS/`, `05_TEMPLATES/`, `06_AGENTS/`, `07_LOGS/` — plus `99_ARCHIVE/`.

## The layers are a trust ordering

The numbering is not filing convenience. It is a gradient from most trusted to least
trusted, and material moves **up** it only through review:

- Input is untrusted by default. It cannot become Knowledge without passing the
  [promotion gate](Canonical-Truth-and-Promotion.md).
- The runtime layer acts under authority ceilings; it does not get to promote its own
  output.
- The evidence layer is what makes any of the above auditable afterward.

Read a directory listing as a claim about trust, not a taxonomy.

## What a vault is

A **vault** is the directory an instance operates on. Core identifies one by markers such as
`00_HOME/` or `.chaseos/`:

```bash
chaseos doctor --vault-root . --json
```

Important for newcomers: **most of Core does not need a vault.** The Gate port, the modality
router, manifest loading, and the secret audit are plain library calls. Only vault-bound
subsystems — capture, the connections registry, workflow execution — require one. The
[examples README](../../examples/README.md) lists which is which.

## Core Rule

Core defines the reusable contract. A personal instance supplies private state, local credentials, live workflows, and operator-specific truth.

That split is enforced rather than encouraged: Core ships no credentials, no personal
content, and no live runtime state, and does not acquire them by being used. See
[Core vs Instance](Core-vs-Instance.md) and
[CORE_MANIFEST.md](../../CORE_MANIFEST.md).

## Related

- [ARCHITECTURE.md](../ARCHITECTURE.md) — the same model as diagrams, plus the module map
- [Repository Layout](../getting-started/Repository-Layout.md) — what each directory holds
- [Authority and Trust](Authority-and-Trust.md) — how the runtime layer is bounded
