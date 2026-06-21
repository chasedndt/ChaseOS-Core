---
type: schema-definition
title: Workspace Schema — Canonical Definition
version: 1.0
created: 2026-03-21
status: active-engineering — schema defined; implemented in Pass 3 (workspace_manager.py)
---

# Workspace Schema — Canonical Definition

> A Workspace groups Source Packages around a coherent topic, project, course, research question, or investigation.
> It is the self-built equivalent of a NotebookLM notebook — owned locally, not platform-dependent.
> Workspaces are SIC runtime objects. They are not vault artifacts unless explicitly promoted.

---

## 1. Purpose

A Workspace serves three functions:

1. **Scoping** — Defines which sources are in scope for a query or output request. SIC retrieval operates within a workspace boundary.
2. **Grouping** — Associates a set of Source Packages around a shared analytical purpose (a project, a course module, a research question, an investigation).
3. **Output tracking** — Records what outputs have been generated for this workspace and their promotion status (intermediate vs. durable vault artifact).

---

## 2. Schema Fields

### 2.1 Identity

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string (UUID v4) | Yes | Unique identifier for this Workspace. Stable. |
| `name` | string | Yes | Human-readable workspace name. User-defined. |
| `description` | string | Yes | Brief description of the workspace topic or purpose. Used as context when generating workspace-level outputs. |
| `created_at` | ISO 8601 datetime | Yes | When this workspace was created. |
| `updated_at` | ISO 8601 datetime | Yes | Last modification timestamp. |
| `status` | enum | Yes | `active` / `archived` / `draft` |

### 2.2 Domain and Project Grouping

| Field | Type | Required | Description |
|---|---|---|---|
| `domain` | string | No | ChaseOS domain this workspace relates to (e.g., `TradingSystems`, `University`, `AI`). Maps to domain index structure. Used when routing promoted outputs to correct `02_KNOWLEDGE/[Domain]/` location. |
| `project_id` | string | No | If this workspace is tied to a specific project in `01_PROJECTS/`, record the project identifier here. |
| `project_name` | string | No | Human-readable project name (denormalized for quick reference). |
| `tags` | array of strings | No | User-defined tags for grouping and search. |

### 2.3 Source Membership

| Field | Type | Required | Description |
|---|---|---|---|
| `source_package_ids` | array of strings | Yes | UUIDs of Source Packages included in this workspace. Order is not significant. |
| `source_count` | integer | Yes | Count of source packages (denormalized for quick reference). |
| `sources_summary` | string | No | Human-readable description of what sources are in this workspace. User-editable. |

**Source membership rules:**
- A Source Package may belong to multiple workspaces.
- Adding or removing a source from a workspace does not delete the Source Package.
- Sources with `user_trust_level: untrusted` may be included in a workspace but their outputs may not be promoted to `02_KNOWLEDGE/`.

### 2.4 Index State

| Field | Type | Required | Description |
|---|---|---|---|
| `index_status` | enum | Yes | `not-indexed` / `indexed` / `stale` (stale = sources changed since last index build) |
| `index_path` | string | No | Path to the workspace-level retrieval index file. |
| `last_indexed_at` | ISO 8601 datetime | No | When the workspace retrieval index was last built or updated. |
| `embedding_model` | string | No | Embedding model used for this workspace's index. If sources use different models, this records the most recent. |

### 2.5 Query Scope

| Field | Type | Required | Description |
|---|---|---|---|
| `query_scope` | enum | Yes | `workspace-only` (default) / `cross-workspace` (deferred — multi-workspace retrieval is Phase 7 out of scope) |
| `retrieval_top_k` | integer | No | Default number of chunks to retrieve per query. Default: 5. |
| `retrieval_min_score` | float | No | Minimum similarity score threshold for retrieved chunks. Provider-specific. |

### 2.6 Allowed Output Classes

| Field | Type | Required | Description |
|---|---|---|---|
| `allowed_output_classes` | array of enum | No | If set, restricts what output types may be generated for this workspace. Default: all output types allowed. See Section 3. |

### 2.7 Outputs

| Field | Type | Required | Description |
|---|---|---|---|
| `outputs` | array of Output Record objects | No | Log of all outputs generated for this workspace. See Section 4 — Output Record. |
| `output_count` | integer | Yes | Count of outputs (denormalized). |

### 2.8 Writeback Rules

| Field | Type | Required | Description |
|---|---|---|---|
| `default_promotion_target` | string | No | Default `02_KNOWLEDGE/[Domain]/` path for promoted outputs from this workspace. Set from `domain` field if not overridden. |
| `promotion_requires_review` | boolean | Yes | Always `true`. Promotion always requires user review — this field exists to prevent any future override. |
| `last_promotion_at` | ISO 8601 datetime | No | Timestamp of last output promoted to vault. |

---

## 3. Output Type Enumeration

These are the output types a Workspace may generate:

| Value | Maps to Knowledge Class | Default Promotion Target |
|---|---|---|
| `source-summary` | `source-derived` | `02_KNOWLEDGE/[Domain]/` source note |
| `faq` | `synthesized` | `02_KNOWLEDGE/[Domain]/` synthesis note |
| `briefing` | `synthesized` | `02_KNOWLEDGE/[Domain]/` synthesis note |
| `study-guide` | `synthesized` | `02_KNOWLEDGE/[Domain]/` synthesis note |
| `timeline` | `synthesized` | `02_KNOWLEDGE/[Domain]/` synthesis note |
| `comparison` | `synthesized` | `02_KNOWLEDGE/[Domain]/` synthesis note |
| `synthesis-draft` | `synthesized` | `02_KNOWLEDGE/[Domain]/` synthesis note |
| `idea-generation` | `generated-ideas` | `02_KNOWLEDGE/[Domain]/` generated-idea note (endorsement_status: unendorsed) |
| `workspace-local-summary` | N/A — intermediate | Workspace record only; no vault artifact |

See `06_AGENTS/SIC-Architecture.md` Section 5 for the canonical routing table.

---

## 4. Output Record Object

Each generated output is recorded in the Workspace's `outputs` array.

| Field | Type | Required | Description |
|---|---|---|---|
| `output_id` | string (UUID v4) | Yes | Unique ID for this output. |
| `output_type` | enum | Yes | See Section 3 above. |
| `title` | string | Yes | Human-readable output title. |
| `generated_at` | ISO 8601 datetime | Yes | When this output was generated. |
| `knowledge_class` | string | Yes | ChaseOS knowledge class this output belongs to. |
| `status` | enum | Yes | `intermediate` / `promoted` / `discarded` |
| `promoted_path` | string | No | If `status: promoted` — the vault path of the promoted markdown artifact (e.g., `02_KNOWLEDGE/TradingSystems/synthesis-note.md`). |
| `promoted_at` | ISO 8601 datetime | No | When promoted. |
| `source_citations` | array of strings | Yes | Source Package IDs + chunk IDs cited in this output. Required for promotion. |
| `evidence_grounded` | boolean | Yes | Whether this output has citations to specific retrieved passages. Outputs with `false` may not be promoted without user adding attribution. |
| `endorsement_status` | enum | No | `unendorsed` / `endorsed`. Required and always `unendorsed` at creation for `idea-generation` outputs. |

---

## 5. Storage

Workspace objects are stored as JSON files:

```
runtime/source_intelligence/workspaces/{workspace_id}/workspace.json
```

Generated output content (the actual text) is stored alongside:

```
runtime/source_intelligence/workspaces/{workspace_id}/outputs/{output_id}.md
```

**Workspaces are never stored inside the Obsidian vault hierarchy** unless the user explicitly promotes a workspace manifest as a knowledge artifact (edge case — the outputs are what get promoted, not typically the workspace object itself).

---

## 6. Workspace Lifecycle

```
1. Create workspace (define name, description, domain)
2. Add source packages (by ID or by ingesting from 03_INPUTS/)
3. Build retrieval index (embed chunks for all sources in workspace)
4. Issue query or request output type
5. SIC retrieves chunks → generates output with citations
6. Output stored as intermediate in workspace
7. User reviews output
8. User promotes (optional) → standard Gate + taxonomy applies
9. Workspace remains active for further queries or is archived
```

---

## 7. Workspace and ChaseOS Domain Structure

Workspaces map to the ChaseOS 18-domain structure through the `domain` field. This drives where promoted outputs land:

| Workspace domain | Promoted output lands in |
|---|---|
| `TradingSystems` | `02_KNOWLEDGE/TradingSystems/` |
| `University` | `02_KNOWLEDGE/University/` |
| `AI` | `02_KNOWLEDGE/AI/` |
| (etc.) | `02_KNOWLEDGE/[Domain]/` |

If `domain` is not set, the user must specify the promotion target during the promotion session.

---

## Related Documents

| Document | Purpose |
|---|---|
| `runtime/source_intelligence/schemas/source_package_schema.md` | Source Package schema — what gets added to a workspace |
| `runtime/source_intelligence/SIC-Provider-Adapter-Standard.md` | Provider adapter — what generates the workspace outputs |
| `06_AGENTS/SIC-Architecture.md` | Full SIC architecture reference |
| `04_SOPS/Promotion-Session-SOP.md` | How to promote workspace outputs to the vault |
| `06_AGENTS/Knowledge-Taxonomy.md` | Six knowledge classes; frontmatter schema |

---

*workspace_schema.md — Version 1.0 | Created: 2026-03-21 | Phase 7 — Source Intelligence Core*


*Graph links: [[OpenClaw-Runtime-Profile]]*
