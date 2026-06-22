# ChaseOS Core

ChaseOS Core is a public framework for building a local-first human-AI operating system. It defines governed memory, source intelligence, agent boundaries, approval workflows, runtime discipline, and evidence-first writeback.

## What This Repository Contains

- Framework documentation for the ChaseOS control plane.
- Templates for notes, projects, logs, runtime profiles, and audits.
- Governance patterns for approval-gated writes.
- Adapter standards for external runtimes.
- Example folders that can be copied into a private deployment.
- Studio product-surface contracts for the app built above Core.

## What This Repository Does Not Contain

- Personal notes or private project state.
- Live runtime logs or approval queues.
- Credential values.
- Provider-specific deployment state.
- Machine-local paths.

## Intended Use

Use Core as a starter kit and reference model. Private deployments should keep local content, runtime state, and operator records outside the public Core tree. ChaseOS Studio should be treated as an application layer over these Core contracts: ship its public contracts and reviewed source-safe docs in the repo, and distribute packaged installers such as `.exe` files through release channels rather than normal source commits.
