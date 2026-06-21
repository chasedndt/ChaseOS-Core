# ChaseOS Core

**MIT-licensed, local-first runtime foundation + framework for ChaseOS.**

ChaseOS Core (formerly "OpenCore") is the public, forkable layer of ChaseOS: the
coordination protocol, persistence seam, schemas, governance contracts, templates,
and local runtime descriptors that the proprietary ChaseOS Studio and ChaseOS Cloud
build on. It contains the *contracts, substrate, and framework scaffold* — not the
proprietary enforcement logic that decides real authority (that lives in the closed
ChaseOS Control Kernel).

> **Status: curated v0 extraction.** Core is carved out of a private monorepo on a
> clean history. It ships the verified import-clean runtime substrate **and** the
> framework/instance-scaffold template layer. More runtime modules are migrated
> module-by-module as their dependencies are untangled — see `EXTRACTION_MANIFEST.md`.

## Your data stays yours

This repository contains **framework + templates + synthetic examples only** — no
personal data. When you run your own instance you create your real notes, projects,
logs, identity (`SOUL.md`), and runtime state locally; those are git-ignored and
**never** belong in a Core fork (see `.gitignore` + `docs/getting-started/Core-vs-Personal.md`).
ChaseOS is local-first: your instance lives on your machine.

## What's here today

**Runtime substrate (`runtime/`, stdlib-first — zero required dependencies):**

- **`agent_bus/`** — N-runtime coordination + task bus, pluggable storage
  (`BusBackend` ABC + `SQLiteBackend` + `backend_loader`, `mode: local | server`).
- **`schedules/`** — native Schedule Intent layer (ChaseOS-owned cron/event intent).
- **`net/`, `security/`, `platform_support/`, `context/`, `lifecycle/`** — SSRF
  egress guard, injection/redaction/prompt-guard, OS abstraction, boot-context
  protocol, local supervisor/descriptors.
- **`execution_adapters/model_config.py`**, **`common/`**, **`adapters/`**
  (codex / n8n / openai) — model-config seam, shared utils, execution adapters.

All verified to have **no compile-time dependency on proprietary ChaseOS modules**
(Studio/Cloud/Control Kernel); optional hooks degrade to no-ops when absent.

**Framework + instance scaffold:**

- `docs/` — getting-started, concepts (Core-vs-Studio-vs-Instance), CLI, agents,
  runtime, governance, workflow standards.
- `05_TEMPLATES/`, `templates/` — note/project/decision/runtime/workflow templates.
- `06_AGENTS/` — governance contracts (Permission-Matrix, Trust-Tiers,
  Agent-Control-Plane, ChaseOS-Gate, Knowledge-Taxonomy, …).
- `00_HOME/*.example.md` + folder READMEs — the empty-instance scaffold.

## Run your own instance

    git clone <your fork>
    python -m venv .venv && . .venv/Scripts/activate   # or source .venv/bin/activate
    pip install -e ".[dev]"
    # scaffold your private vault folders/files from the templates (non-destructive):
    chaseos setup init --write
    pytest

Then fill in your own `SOUL.md`, `00_HOME/Now.md`, projects, and knowledge — all
local, all git-ignored.

## What ChaseOS Core is not

- It is **not** ChaseOS Studio (the proprietary desktop product).
- It is **not** ChaseOS Cloud (the proprietary managed-services umbrella).
- It does **not** contain the Control Kernel's authority-enforcement logic.
- It is **not** production-ready autonomy.

## Licence

MIT — see `LICENSE`. "ChaseOS" and associated logos are trademarks of ChaseOS Ltd.
(see `TRADEMARKS.md`). Third-party runtimes (Hermes, OpenClaw) are independent
upstream MIT projects integrated via governed adapters — see `THIRD_PARTY_NOTICES.md`.
A dependency inventory is in `SBOM.md`.

See `CONTRIBUTING.md` (external contributions are not yet open) and `GOVERNANCE.md`.
