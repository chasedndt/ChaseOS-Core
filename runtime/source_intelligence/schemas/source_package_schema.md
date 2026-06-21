---
type: schema-definition
title: Source Package Schema — Canonical Definition
version: 1.1
created: 2026-03-21
updated: 2026-03-21
status: implemented — builder (Pass 2), workspace manager (Pass 3), index layer (Pass 4) all operational
---

# Source Package Schema — Canonical Definition

> A Source Package is the normalized internal representation of any source ingested into the Source Intelligence Core.
> Every source becomes a Source Package before any SIC operation acts on it.
> The Source Package is the universal intake object — source type does not change the schema contract.

---

## 1. Purpose

The Source Package serves three functions:

1. **Normalization** — Converts heterogeneous source types (PDF, transcript, webpage, etc.) into a consistent internal object that the retrieval and output layers can operate over without knowing the source type.
2. **Provenance** — Maintains an unbroken chain from the normalized text representation back to the origin file in `03_INPUTS/` or a local storage path. Source files are never embedded inside the schema — they are referenced.
3. **Trust accounting** — Records whether the source has been through the injection scan (see `04_SOPS/Untrusted-Input-Handling-SOP.md`) and what the user's confidence level is in the material.

---

## 2. Schema Fields

### 2.1 Identity

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string (UUID v4) | Yes | Unique identifier for this Source Package. Stable across workspace membership changes. |
| `title` | string | Yes | Human-readable source title. User-editable. |
| `source_type` | enum | Yes | See Section 3 — Source Type Enumeration |
| `created_at` | ISO 8601 datetime | Yes | When this Source Package was created |
| `updated_at` | ISO 8601 datetime | Yes | Last modification timestamp |

### 2.2 Provenance

| Field | Type | Required | Description |
|---|---|---|---|
| `origin_path` | string | Yes | Absolute path to the origin file in `03_INPUTS/` or local filesystem. Source files are never embedded in the package — this reference is the link. |
| `origin_url` | string | No | Original URL for web clips, research digests, or online sources. Empty for local-only files. |
| `author` | string | No | Author or speaker (for transcripts). |
| `publication_date` | date (YYYY-MM-DD) | No | When the source material was originally published or recorded. |
| `intake_date` | date (YYYY-MM-DD) | Yes | When the source entered `03_INPUTS/`. |
| `package_created_date` | date (YYYY-MM-DD) | Yes | When SIC built the Source Package from the origin file. |

### 2.3 Extraction Status

| Field | Type | Required | Description |
|---|---|---|---|
| `extraction_status` | enum | Yes | `pending` / `in-progress` / `complete` / `failed` |
| `extraction_method` | string | No | How text was extracted: `direct-text`, `pdf-parse`, `transcript-import`, `web-extract`, `clipboard-paste` |
| `extraction_notes` | string | No | Any warnings, partial failures, or manual fixes applied during extraction. |
| `chunk_count` | integer | No | Number of chunks created from the normalized text. Populated after chunking. |

### 2.4 Normalized Text Representation

| Field | Type | Required | Description |
|---|---|---|---|
| `normalized_text` | string | Yes | The full extracted, cleaned text content. This is what retrieval operates over — not the origin file. |
| `normalized_text_char_count` | integer | Yes | Character count of normalized text. |
| `chunks` | array of Chunk objects | No | Created during indexing. See Section 4 — Chunk Object. |

### 2.5 Trust State

| Field | Type | Required | Description |
|---|---|---|---|
| `injection_scan_status` | enum | Yes | `not-scanned` / `scanned-clean` / `scanned-flagged` |
| `injection_scan_notes` | string | No | Any flags raised during the injection scan. |
| `user_trust_level` | enum | Yes | `untrusted` / `reviewed` / `trusted`. Default: `untrusted`. Must be set to `reviewed` or `trusted` before SIC may generate outputs for promotion. |
| `trust_notes` | string | No | User notes on why this source is trusted or untrusted. |

**Trust rules:**
- Sources with `user_trust_level: untrusted` may be processed for workspace-local intermediate outputs only.
- Sources with `injection_scan_status: scanned-flagged` must not be promoted to `02_KNOWLEDGE/` without explicit user review.
- Trust state must be set by the user, not inferred by SIC.

### 2.6 Workspace Assignment

| Field | Type | Required | Description |
|---|---|---|---|
| `workspace_ids` | array of strings | No | UUIDs of workspaces this source package belongs to. A source may belong to multiple workspaces. |
| `workspace_labels` | array of strings | No | Human-readable workspace names (denormalized for quick reference). |

### 2.7 Index State

| Field | Type | Required | Description |
|---|---|---|---|
| `embedding_status` | enum | Yes | `not-embedded` / `embedded` / `stale` |
| `embedding_model` | string | No | Model used to generate embeddings (e.g., `text-embedding-3-small`, `nomic-embed-text`). |
| `index_path` | string | No | Path to the local embedding index file for this source's chunks. |
| `last_indexed_at` | ISO 8601 datetime | No | When embedding was last generated. |

---

## 3. Source Type Enumeration

| Value | Description |
|---|---|
| `pdf` | PDF document — extracted via PDF parser |
| `plain-text` | .txt or raw text file |
| `markdown` | .md file (local notes, exports) |
| `webpage` | Extracted text from a URL (web clip) |
| `transcript-verbatim` | Full verbatim transcript (YouTube captions, lecture, meeting, podcast) |
| `transcript-summary` | A summary or condensed version of a transcript (not verbatim) |
| `research-digest` | Perplexity, Grok, newsletter digest, or curated AI output |
| `clipboard-paste` | Text pasted/clipped from any source |
| `audio-derived` | Text derived from audio/caption processing |
| `document-export` | Exported local document (Word, Notion, etc.) |

**YouTube scoping note:** For Phase 7, YouTube sources are supported as `transcript-verbatim` (captions-derived text) with metadata and source reference preserved. Full video media-awareness is out of scope until a future phase.

---

## 4. Chunk Object

Chunks are created during embedding and retrieval indexing. They are stored inside the Source Package's `chunks` array.

| Field | Type | Required | Description |
|---|---|---|---|
| `chunk_id` | string | Yes | Unique ID within this source package (e.g., `{source_id}_c001`) |
| `chunk_index` | integer | Yes | Position in the source (0-indexed) |
| `text` | string | Yes | The chunk text content |
| `char_count` | integer | Yes | Character count of this chunk |
| `section_heading` | string | No | The nearest section heading above this chunk (for heading-aware chunking) |
| `embedding_vector` | array of floats | No | Dense embedding vector (populated during indexing) |
| `embedding_model` | string | No | Model used to generate this embedding |

**Chunking strategy (default):** Section or paragraph-level chunking with overlap. Exact parameters defined in the pipeline configuration (`runtime/source_intelligence/pipelines/`).

---

## 5. Storage

Source Package objects are stored as JSON files in:

```
runtime/source_intelligence/workspaces/{workspace_id}/source_packages/{slug}_{hash_prefix}.json
```

The `slug` is derived from the source filename stem; `hash_prefix` is the first 12 hex characters of the SHA-256 content hash.

**Legacy note:** Early Pass 2 test packages were written to `sources/` (now deprecated). New packages always go to `source_packages/`. The workspace manager and index manager support both paths for backward compatibility.

**Source packages are never stored inside the Obsidian vault hierarchy.** They are runtime objects. Only promoted outputs become vault artifacts.

---

## 6. Relation to `03_INPUTS/`

The `origin_path` field always points back to the file in `03_INPUTS/` (or the local filesystem path). The Source Package contains the normalized text representation — it does not copy or embed the raw file.

If the origin file is deleted or moved, `extraction_status` becomes `stale` and the package must be rebuilt.

---

## 7. Relation to the Knowledge Taxonomy

Source Packages are not vault artifacts and do not carry taxonomy frontmatter. They are SIC runtime objects.

When a SIC output is promoted to `02_KNOWLEDGE/`, the promoted markdown note carries the full taxonomy frontmatter. The source package is referenced by ID in the note's frontmatter.

| Source Package field | Promoted note field |
|---|---|
| `id` | `source_package_id` (in promoted note frontmatter) |
| `title` | Used to populate note title and frontmatter |
| `source_type` | Informs `knowledge_class` selection |
| `origin_url` | Becomes source link in note |
| `user_trust_level` | Must be `reviewed` or `trusted` before promotion |

---

## Related Documents

| Document | Purpose |
|---|---|
| `runtime/source_intelligence/schemas/workspace_schema.md` | Workspace schema — how Source Packages are grouped |
| `runtime/source_intelligence/SIC-Provider-Adapter-Standard.md` | Provider adapter — what processes the normalized text |
| `06_AGENTS/SIC-Architecture.md` | Full SIC architecture reference |
| `04_SOPS/Untrusted-Input-Handling-SOP.md` | Injection scan procedure that sets `injection_scan_status` |
| `06_AGENTS/Knowledge-Taxonomy.md` | Six knowledge classes; frontmatter schema for promoted outputs |

---

*source_package_schema.md — Version 1.0 | Created: 2026-03-21 | Phase 7 — Source Intelligence Core*


*Graph links: [[OpenClaw-Runtime-Profile]]*
