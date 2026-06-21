---
type: runtime-readme
title: Source Intelligence Core — Runtime Directory
version: 1.0
created: 2026-03-21
status: complete — all 7 passes done 2026-03-26; Phase 8 (connector/capture) is next phase
---

# Source Intelligence Core — Runtime Directory

> This directory is the local-first runtime store for the Source Intelligence Core (SIC).
> It is a ChaseOS subsystem directory. It does not replace the vault — it feeds it.

---

## What Lives Here

| Subdirectory | Purpose |
|---|---|
| `schemas/` | Canonical schema definitions: Source Package, Workspace, output types |
| `workspaces/` | Workspace object files (local, not Obsidian-visible unless promoted) |
| `providers/` | Provider adapter configuration and manifests |
| `pipelines/` | Pipeline definitions and processing configurations |
| `indexes/` | Local embedding indexes (never sent to a third-party storage service) |

---

## What Does NOT Live Here

- Source files (PDFs, transcripts, documents) → `03_INPUTS/`
- Promoted durable knowledge artifacts → `02_KNOWLEDGE/[Domain]/`
- Project canonical state → `01_PROJECTS/`
- Sprint focus → `00_HOME/Now.md`

The vault is the durable memory. This directory is the runtime layer that produces content for the vault.

---

## Data Flow

```
03_INPUTS/               → SIC ingests sources from here
  ↓
runtime/source_intelligence/   → SIC processing: normalize, chunk, embed, workspace, generate
  ↓
02_KNOWLEDGE/[Domain]/   → SIC outputs promoted to here via standard Gate + taxonomy
```

---

## Local-First Principle

Everything in this directory stays on the user's system:
- Source packages (normalized text + chunks)
- Embedding indexes
- Workspace objects
- Generated workspace-local outputs

Provider adapters (Claude, OpenAI, local Ollama) receive source text for current operations only. They do not store workspace state or session history.

---

## Gate Enforcement

Promotion from workspace-local intermediate to durable vault artifact requires:
- `CHASEOS_PROMOTION_APPROVED=1` in the executing shell
- Standard ingestion promotion guard applies
- Full taxonomy frontmatter required on all promoted notes
- Domain index update required in same session

See `04_SOPS/Promotion-Session-SOP.md`.

---

## Architecture Reference

The canonical architecture for this subsystem is: `06_AGENTS/SIC-Architecture.md`

---

## Embedding Backends (Pass 7)

Three embedding backends are available. Local backends require no credentials.

| Backend | Quality | Deps | Notes |
|---------|---------|------|-------|
| `local_stub` | none (hash) | none | Default fallback. Deterministic. For testing only. |
| `local_word` | lexical | none | Feature hashing over words. Meaningful for real queries. Recommended local option. |
| `openai` | semantic | `pip install openai` + `OPENAI_API_KEY` | Full semantic embeddings. Opt-in only. |

CLI:
```
# List all backends and availability
python -m runtime.source_intelligence.indexes.index_manager list-backends

# Index with a specific backend
python -m runtime.source_intelligence.indexes.index_manager index-workspace \
  --workspace phase7-test --backend local_word --force

# Run retrieval benchmark
python -m runtime.source_intelligence.retrieval.retriever benchmark \
  --workspace phase7-test
```

---

*runtime/source_intelligence/Source-Intelligence-Folder-Guide.md — Version 1.1 | Created: 2026-03-21 | Updated: 2026-03-26 | Phase 7 COMPLETE (all 7 passes)*


*Graph links: [[OpenClaw-Runtime-Profile]]*
