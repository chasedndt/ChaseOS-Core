---
title: Provenance Migration Notes
type: migration-notes
status: seeded — partial-lineage doctrine
version: 0.1
created: 2026-04-24
updated: 2026-04-24
owner: Optimus
phase: Phase 9 second-wave
---

# Provenance Migration Notes

This document records how ChaseOS should treat provenance for artifacts that predate the full provenance substrate.

It exists to prevent two failure modes:
1. pretending historical artifacts already have complete lineage when they do not
2. blocking all older artifacts forever because they were created before the provenance schema existed

The correct posture is **honest partial retrofit**.

---

## 1. Core Rule

A partial lineage is allowed.
A fabricated complete lineage is not.

If ChaseOS can recover some truthful lineage anchors for an older artifact, it should preserve and expose them.
If ChaseOS cannot reconstruct a step reliably, it should mark that step as unknown or missing rather than guessing.

---

## 2. What Can Usually Be Retrofitted Now

These are the lineage anchors ChaseOS can often recover from the current repo without inventing history.

### A. Source package identifiers
Often recoverable from:
- promoted note frontmatter such as `source_package_id`
- SIC workspace artifacts under `runtime/source_intelligence/workspaces/`
- explicit source package refs in runtime JSON artifacts

### B. Acquisition artifact identifiers
Often recoverable from:
- `artifact_id` fields in `runtime/acquisition/packs/`
- `source_packet_refs`
- `normalized_source_pack_ref`
- `briefing_ready_input_set` references

### C. Build-log references
Often recoverable from:
- `07_LOGS/Build-Logs/`
- explicit implementation/build notes naming the artifact family or pass
- follow-on logs that mention outputs, reports, or generated artifacts

### D. Agent-activity references
Often recoverable from:
- `07_LOGS/Agent-Activity/`
- audit refs stored inside runtime artifacts
- workflow output references recorded by AOR

### E. Capture IDs, hashes, and sidecar refs
Recoverable where already present via:
- Phase 8 sidecars
- `content_sha256`
- `sidecar_ref`
- `capture_id`
- quarantine paths under `03_INPUTS/00_QUARANTINE/`

### F. Promotion anchors in knowledge-note frontmatter
Many existing promoted notes already preserve minimum useful provenance anchors such as:
- `promoted_from`
- `verification_status`
- `source_package_id`
- trust-level or source-trust context

These do not form a full lineage chain by themselves, but they are still valid provenance evidence.

---

## 3. What Cannot Usually Be Reconstructed Perfectly

These gaps should be treated as normal historical limits, not as failures to be hidden.

### A. Missing transformation steps
Older artifacts may not record:
- every normalization step
- every summarization/synthesis step
- each intermediate runtime-local representation

### B. Outputs created before the provenance schema existed
Some artifacts were created before a common provenance envelope or validator existed.
Those artifacts may still have references, but not a standardized chain.

### C. Incomplete verification-status history
A note may show its current `verification_status` without preserving every earlier state transition.
Current posture may be known even when the full review timeline is not.

### D. Missing run-local context
Some historical artifacts may lack:
- exact workflow manifest snapshot
- exact role-card snapshot
- exact task inputs
- exact runtime identity for older runs

### E. Lost or moved intermediate artifacts
When temporary files were never preserved, later systems must not imply that the missing layer still exists.

---

## 4. Migration Posture by Artifact Family

| Artifact family | Migration posture |
|---|---|
| `02_KNOWLEDGE/` notes | Preserve frontmatter anchors like `promoted_from`, `verification_status`, and any source package refs; do not invent absent chain steps |
| `runtime/source_intelligence/` source packages | Treat package IDs and origin refs as strong lineage anchors |
| `runtime/acquisition/packs/` artifacts | Treat `artifact_id`, packet refs, pack refs, audit refs, and transformation chains as strong first-wave provenance substrate |
| `07_LOGS/Build-Logs/` | Use as chronology and implementation evidence, not as proof of source truth by themselves |
| `07_LOGS/Agent-Activity/` | Use as execution/audit evidence, not as proof that content claims are true |
| `03_INPUTS/00_QUARANTINE/` + sidecars | Treat as raw-source/capture evidence where refs and hashes exist |
| future trace reports | Always label themselves as derivative outputs built from currently recoverable lineage |

---

## 5. Rules for Future Retrofitting

When adding provenance to an older artifact family:
1. preserve original known identifiers
2. attach recovered refs explicitly
3. label inferred boundaries as unknown when evidence is incomplete
4. never replace stronger source evidence with weaker summary evidence
5. prefer append-only provenance additions over silent mutation of historical meaning

---

## 6. Relationship to `trace_idea`

`trace_idea` should use this migration posture when traversing older artifacts.

That means:
- trace what is currently provable
- surface gaps explicitly
- distinguish source artifacts from derived summaries and reports
- avoid presenting chronology completeness as lineage completeness

---

## 7. Relationship to Gate Provenance Minimums

The current Gate-adjacent provenance seam is intentionally narrow.
It does not require a fully modern provenance block on every promoted note.

This migration document explains why:
- many older knowledge notes can honestly satisfy minimum provenance posture through fields like `verification_status` plus `promoted_from`
- requiring full modern provenance immediately would create a fake maturity cliff
- richer provenance can be added over time without falsifying historical continuity

---

## 8. Current Verdict

ChaseOS should migrate provenance forward the same way it handles constitutional truth everywhere else:
- preserve what is known
- distinguish what is inferred
- expose what is missing
- never counterfeit completeness

That is how historical artifact continuity remains compatible with a stronger future provenance substrate.

---

*Graph links: [[Provenance-Schema-and-Trace-Idea-Implementation-Plan]] · [[Normalization-Provenance-Contract]] · [[Provenance-Explorer-and-Chronology-Browser-Standalone-Application]] · [[Feature-Fit-Register]]*