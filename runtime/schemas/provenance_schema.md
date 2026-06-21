---
type: schema-definition
title: Provenance Schema — Canonical Definition
version: 0.1
created: 2026-04-24
updated: 2026-04-24
status: seeded — Phase 9 second-wave implementation foothold
---

# Provenance Schema — Canonical Definition

> A provenance block is the minimum machine-readable lineage object ChaseOS attaches to notes, outputs, source packages, and traceable runtime artifacts.
> It records where something came from, what stage it is in, how it got there, and what audit references support that path.

---

## 1. Purpose

The provenance schema serves four functions:
1. **Lineage** — record the source IDs and transformation path for an artifact.
2. **Verification posture** — preserve whether an artifact is unverified, operator-reviewed, cross-referenced, or verified.
3. **Traceability** — support read-only lineage traversal such as `trace_idea`.
4. **Governance** — provide the minimum substrate future Gate/CGL logic can consult without inventing missing history.

---

## 2. Required Fields

| Field | Type | Description |
|---|---|---|
| `source_ids` | list[str] | Stable IDs of upstream sources or source packages contributing to the artifact |
| `processing_stage` | string | Current stage of the artifact in the ChaseOS lifecycle |
| `verification_status` | string | Current verification posture |
| `lineage_chain` | list[object] | Ordered transformation entries from upstream source to current artifact |
| `created_at` | ISO-8601 string | When this provenance block was first attached |
| `last_modified_at` | ISO-8601 string | Most recent provenance update timestamp |
| `operator_reviewed_at` | ISO-8601 string or null | When an operator review last occurred |
| `source_refs` | list[str] | File/path/record refs to the source-side objects |
| `audit_refs` | list[str] | File/path/record refs to relevant audit/build/runtime records |

---

## 3. Processing Stage Vocabulary

Allowed values:
- `raw_capture`
- `quarantine`
- `normalized`
- `source_package`
- `briefing_input`
- `generated`
- `reviewed`
- `promoted`
- `canonical`

These stages are append-oriented lifecycle markers.
They do not erase earlier history.

---

## 4. Verification Status Vocabulary

Allowed values:
- `unverified`
- `operator_reviewed`
- `cross_referenced`
- `verified`

Verification status is never inferred as a substitute for audit evidence.
It must be operator-authored or runtime-authored with an audit trail.

---

## 5. Lineage Chain Entry Shape

Each `lineage_chain` entry must include:
- `stage`
- `ref`
- `timestamp`

Recommended future extension fields:
- `action`
- `runtime_id`
- `workflow_id`
- `notes`

Example:
```yaml
lineage_chain:
  - stage: raw_capture
    ref: 03_INPUTS/00_QUARANTINE/source/example.md
    timestamp: 2026-04-24T00:00:00Z
  - stage: source_package
    ref: runtime/source_intelligence/workspaces/demo/source_packages/example_123.json
    timestamp: 2026-04-24T00:05:00Z
  - stage: generated
    ref: 07_LOGS/Operator-Briefs/2026-04-24-example.md
    timestamp: 2026-04-24T00:10:00Z
```

---

## 6. Relationship to Existing ChaseOS Layers

### Phase 8 Capture
- `capture_id` and SHA-256 fields in sidecars are provenance anchors.
- Provenance blocks should reference them, not duplicate raw content.

### Phase 7 SIC Source Packages
- `source_package` stage entries should point to real source package artifacts.
- Source package IDs are lineage nodes, not just filenames.

### Phase 9 Acquisition + Normalization
- acquisition artifacts can appear as `normalized` or `briefing_input` steps.
- provenance should reflect real transformation points from acquisition packs into downstream outputs.

### Gate / CGL
- future provenance minimum checks and Context Governance Layer logic consume these fields.

---

## 7. Governance Rules

1. Provenance is **append-only**.
2. Provenance blocks record IDs, refs, and stage markers — not raw external content.
3. Partial lineage is allowed when history is incomplete.
4. Fabricated complete lineage is forbidden.
5. Schema changes require explicit migration discipline.

---

## 8. Current Verdict

The provenance schema is the minimum lineage substrate ChaseOS needs before `trace_idea`, provenance-aware Gate checks, and later Provenance Explorer surfaces can be implemented honestly.

---

*Graph links: [[Normalization-Provenance-Contract]] · [[Provenance-Schema-and-Trace-Idea-Implementation-Plan]] · [[06_AGENTS/ChaseOS-Gate|ChaseOS-Gate]]*
