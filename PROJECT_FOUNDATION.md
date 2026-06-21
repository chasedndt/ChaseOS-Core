# PROJECT_FOUNDATION — ChaseOS Core

ChaseOS is a privacy-first agentic operating system where humans and AI agents work
together through secure memory, knowledge graphs, automations, and permissioned
execution. **ChaseOS Core** is its MIT-licensed, local-first foundation.

This document is framework-level and public-safe. It contains no personal-instance
content. For the conceptual deep-dives see `docs/concepts/` and `docs/getting-started/`.

## 1. The product family

ChaseOS ships as layered products with deliberate licensing:

| Layer | License | What it is |
|---|---|---|
| **ChaseOS Core** | MIT | This repo — engine, contracts, schemas, governance docs, instance templates |
| **Chaser Agent** | MIT | First-party local-first runtime harness (review-first source loop) |
| **ChaseOS Studio** | Proprietary | Desktop command / experience surface (Community / Pro / Teams) |
| **ChaseOS Cloud** | Proprietary | Optional managed services (providers & tools, sync, compute, managed runtimes, deploy) |
| **Control Kernel** | Proprietary | Authority-enforcement logic |

The open layers (Core + Chaser Agent) drive adoption and define the standards; the
proprietary layers (Studio / Cloud / Control Kernel) provide the paid command surface,
managed convenience, and enforced governance.

> **Dependency direction is one-way.** Core never imports or depends on the proprietary
> layers. Studio/Cloud/Control Kernel build *on top of* Core. This is verified during
> extraction (see `EXTRACTION_MANIFEST.md`).

## 2. What Core provides

- **Coordination substrate** — the Agent Bus (N-runtime task/coordination bus with a
  pluggable storage backend) and the native Schedule Intent layer.
- **Runtime support** — boot-context protocol, lifecycle descriptors, OS/platform
  abstraction.
- **Safety primitives** — SSRF egress guard, prompt-injection scanning, secret
  redaction, prompt guards.
- **Provider-agnostic execution seam** — model-config + execution-adapter contracts so
  models/runtimes are swappable.
- **Governance contracts** — Permission Matrix, Trust Tiers, Agent Control Plane,
  ChaseOS Gate, Knowledge Taxonomy.
- **Framework + instance scaffold** — folder conventions, templates, synthetic
  examples, SOPs, and getting-started docs so a forker can stand up their own instance.

## 3. Core vs Personal — the instance model

ChaseOS distinguishes **reusable framework material (Core)** from a **private live
instance (Personal)**:

- **Core** = what this repo ships: architecture, governance, runtime standards,
  neutral templates, synthetic examples, safe setup/CLI docs.
- **Personal** = your private operating truth: active projects, source notes, logs,
  identity (`SOUL.md`), credentials, runtime memory and state.

When you run ChaseOS you create your Personal instance **locally** (scaffolded from the
templates via `chaseos setup init --write`). Personal content is git-ignored and never
belongs in a Core fork; ChaseOS Studio resolves your vault on your own machine at
runtime and never bundles it. See `docs/getting-started/Core-vs-Personal.md` and
`docs/concepts/Core-vs-Studio-vs-Instance.md`.

## 4. Design principles

- **Local-first by default** — your graph and data live on your machine.
- **Stdlib-first** — Core has zero required runtime dependencies (see `SBOM.md`).
- **Approval-bound execution** — sensitive actions are gated; no silent external effects.
- **Provider-agnostic** — models generate; the graph is durable. No model owns your
  workspace architecture.
- **Inspectable and auditable** — sources, decisions, runs, and approvals are explicit.

## 5. What Core is not

- Not ChaseOS Studio (proprietary desktop product).
- Not ChaseOS Cloud (proprietary managed services).
- Not the Control Kernel's authority-enforcement logic.
- Not production-ready autonomy, a hosted service, or a public installer.

## 6. Status and growth

Core is a **curated v0 extraction**: the verified import-clean substrate + the
framework/instance-scaffold layer. It grows module-by-module as deeper runtime modules
are decoupled from the proprietary layers. See `ROADMAP.md` for direction and
`EXTRACTION_MANIFEST.md` for the extraction method.
