---
type: adapter-standard
title: SIC Provider Adapter Standard
version: 1.0
created: 2026-03-21
status: declared-scope — standard defined; adapter implementations pending
---

# SIC Provider Adapter Standard

> This document defines the boundary between the Source Intelligence Core and its pluggable model backend.
> The provider adapter supplies generation and embeddings. It does not own workspace logic, source schema, retrieval decisions, output classification, writeback routing, or Gate enforcement.
> ChaseOS owns the orchestration and data model. The adapter is interchangeable.

---

## 1. What the Provider Adapter Is

The SIC Provider Adapter is the pluggable interface through which the Source Intelligence Core sends text to a model backend for generation or embedding, and receives a structured result back.

**What the adapter provides:**
- Text generation (summaries, FAQs, synthesis drafts, Idea Generation notes, etc.)
- Text embeddings for chunk indexing and retrieval scoring
- Optionally: audio transcription (if provider supports it and it is configured)
- Optionally: tool use / structured output (if required by SIC pipeline and supported)

**What the adapter does NOT provide or own:**
- Workspace logic — which sources are in scope
- Source Package schema — how sources are normalized and stored
- Retrieval decisions — which chunks to surface for a query
- Output classification — what knowledge class an output belongs to
- Writeback routing — where output lands in the ChaseOS vault
- Gate enforcement — `CHASEOS_PROMOTION_APPROVED=1` gate logic
- Taxonomy frontmatter — required on all promoted notes
- Domain index updates — required at promotion time
- Session logging — ChaseOS build log and audit trail

These all belong to ChaseOS. The adapter is called by ChaseOS, not the reverse.

---

## 2. How the Adapter Fits Into the SIC

```
ChaseOS SIC
  ↓
  Workspace + Retrieval Layer (selects chunks, builds prompt)
  ↓
  Provider Adapter Interface ← [adapter boundary]
  ↓
  Model Backend (Claude, OpenAI, local Ollama, etc.)
  ↑
  Provider Adapter Interface ← [response back]
  ↑
  SIC Output Generation Layer (classifies, structures, cites)
  ↑
  ChaseOS Gate + Taxonomy (if promotion requested)
```

The adapter is a thin I/O layer. It receives a prompt (constructed by SIC) and returns a response. It does not independently decide what to do with source material, workspace state, or generated outputs.

---

## 3. Distinction from Phase 5 Execution Adapters

**This is a different concept.** These two adapter layers must not be confused.

| | Phase 5 Execution Adapter | SIC Provider Adapter |
|---|---|---|
| **Layer** | How an agent OPERATES in the ChaseOS vault (routing, permissions, writeback) | How SIC sends text to a model and receives output |
| **Defined in** | `06_AGENTS/Execution-Adapter-Standard.md` | This document |
| **Per-backend docs** | `CLAUDE.md`, `OPENAI.md`, `LOCAL-OSS.md`, `N8N.md` | `runtime/source_intelligence/providers/` |
| **Controls** | Agent behavior, vault permissions, session patterns | Generation + embedding calls only |
| **Scope** | Full agent operation | SIC text I/O only |

Both layers are active simultaneously. The Phase 5 execution adapter governs how Claude Code operates in the vault. The SIC provider adapter governs how the Source Intelligence Core calls a model backend for generation/embedding.

---

## 4. Provider Adapter Interface Contract

Every provider adapter must implement:

### 4.1 `generate(prompt, config) → GenerationResult`

Accepts:
- `prompt` — the full constructed prompt from the SIC pipeline (including retrieved chunks and instructions)
- `config` — generation parameters: model, temperature, max_tokens, output_format (if structured output requested)

Returns:
- `text` — the generated output text
- `model_used` — which model produced this (for audit trail)
- `token_count` — input + output token counts (for cost tracking)
- `error` — null if success; error message if failure

### 4.2 `embed(texts[], config) → EmbeddingResult[]`

Accepts:
- `texts[]` — array of chunk texts to embed
- `config` — embedding model name, dimensions

Returns per text:
- `vector` — the dense embedding vector (array of floats)
- `model_used` — embedding model name
- `error` — null if success; error message if failure

### 4.3 `transcribe(audio_path, config) → TranscriptionResult` *(optional)*

Only required for adapters that support audio transcription (e.g., OpenAI Whisper). Not required for Phase 7 baseline.

---

## 5. What the Adapter Receives

The SIC constructs the full prompt before calling the adapter. The adapter receives:

- Assembled context (retrieved chunk texts, with source references)
- An instruction block (what output type is requested, format requirements, citation requirements)
- A task description (the user's query or output type request)

The adapter does NOT receive raw source files. It receives the text the SIC pipeline has already extracted, chunked, retrieved, and assembled.

**Minimum-text principle:** The adapter receives only the text required for the current operation. It does not receive the full workspace source set unless all sources are needed for the operation.

---

## 6. Local-First Storage Principle

| Asset | Adapter's responsibility |
|---|---|
| Raw source files | Not the adapter's concern — stays in `03_INPUTS/` |
| Source Package normalized text | Not stored by adapter — stays in SIC runtime |
| Embedding vectors | Returned to SIC; stored locally in `runtime/source_intelligence/indexes/` |
| Workspace objects | Not the adapter's concern — stays in `runtime/source_intelligence/workspaces/` |
| Generated output text | Returned to SIC; stored in workspace output record |
| Session history | Not stored by adapter — ChaseOS build log is the record |

**The adapter is stateless from the SIC's perspective.** Each call is independent. The adapter does not maintain session state, workspace state, or cross-call memory on behalf of SIC.

---

## 7. Credential Handling

Credentials for remote providers (API keys) follow `04_SOPS/Credential-Boundaries-SOP.md`.

Key rules:
- API keys are never stored in vault files (markdown, YAML frontmatter, etc.)
- API keys are set as environment variables or via the provider's CLI config
- Local provider adapters (Ollama) require no external credential — this is the default offline path

---

## 8. Supported Provider Paths

### 8.1 Anthropic / Claude (Phase 7 primary)

- Generation: Claude API (claude-sonnet-4-6 or later as default)
- Embeddings: Voyage AI (Anthropic's recommended embedding provider) or OpenAI fallback
- Configuration: `runtime/source_intelligence/providers/anthropic.yaml`

### 8.2 OpenAI (alternative)

- Generation: GPT-4o or later
- Embeddings: `text-embedding-3-small` (default) or `text-embedding-3-large`
- Configuration: `runtime/source_intelligence/providers/openai.yaml`

### 8.3 Local / Ollama (offline path)

- Generation: Any Ollama-compatible model (e.g., `llama3.2`, `mistral`, `phi3`)
- Embeddings: `nomic-embed-text` (Ollama) or `mxbai-embed-large`
- Configuration: `runtime/source_intelligence/providers/local.yaml`
- No external credential required
- Latency and quality tradeoff vs. remote providers — user's choice

### 8.4 Switching providers

Because ChaseOS owns the source packages, workspace objects, and vault artifacts — switching providers does not require migrating data. Source packages are provider-agnostic. Embedding indexes may need to be rebuilt if the embedding model changes (embedding vector dimensions are model-specific).

---

## 9. Provider Configuration Files

Each active provider has a YAML config in:

```
runtime/source_intelligence/providers/{provider_name}.yaml
```

Fields:
- `provider` — anthropic / openai / local
- `generation_model` — default model for generation
- `embedding_model` — default model for embeddings
- `endpoint` — API base URL (for local providers or custom endpoints)
- `max_retries` — retry limit on API failure
- `timeout_seconds` — request timeout

Credentials are NOT stored in these files.

---

## Related Documents

| Document | Purpose |
|---|---|
| `06_AGENTS/SIC-Architecture.md` | Full SIC architecture; Layer 5 describes the adapter's role |
| `06_AGENTS/Execution-Adapter-Standard.md` | Phase 5 execution adapter — different layer, do not confuse |
| `runtime/source_intelligence/schemas/source_package_schema.md` | What the adapter processes (via SIC pipeline) |
| `04_SOPS/Credential-Boundaries-SOP.md` | Credential handling rules |
| `runtime/source_intelligence/providers/` | Per-provider YAML configuration files |

---

*SIC-Provider-Adapter-Standard.md — Version 1.0 | Created: 2026-03-21 | Phase 7 — Source Intelligence Core*


*Graph links: [[OpenClaw-Runtime-Profile]]*
