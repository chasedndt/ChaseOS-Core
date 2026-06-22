---
type: template
title: Adapter Compliance Checklist
version: 1.0
created: 2026-03-20
scope: framework-level
---

# Adapter Compliance Checklist

> Use this checklist when activating, auditing, or upgrading an execution adapter.
> An adapter must pass all items in its tier and all tiers below it.
> Gate architecture: `[[06_AGENTS/ChaseOS-Gate|ChaseOS-Gate]]` · Manifest standard: `[[06_AGENTS/Adapter-Manifest-Standard|Adapter-Manifest-Standard]]` · Permission rules: `[[06_AGENTS/Permission-Matrix|Permission-Matrix]]`

---

## How to Use This Checklist

1. Identify the adapter's intended capability tier (see below)
2. Complete all items in Tier 1 (all adapters)
3. Complete all items in applicable upper tiers
4. Record results in the adapter's manifest (`runtime/policy/adapters/[adapter-id].yaml`)
5. If any item fails, document the gap and do not advance the adapter's status to `active`

---

## Tier 1 — All Adapters (Advisory and Above)

These apply to every execution surface operating in or adjacent to ChaseOS, regardless of vault access level.

- [ ] Adapter has a markdown document (CLAUDE.md / OPENAI.md / LOCAL-OSS.md / N8N.md or equivalent)
- [ ] Adapter has a manifest file at `runtime/policy/adapters/[adapter-id].yaml`
- [ ] Manifest is complete (all required fields per `[[06_AGENTS/Adapter-Manifest-Standard|Adapter-Manifest-Standard]]`)
- [ ] Adapter has a registry entry in `06_AGENTS/Agent-Registry.md`
- [ ] Trust tier is explicitly declared and does not exceed the adapter's surface class ceiling
- [ ] Credential handling rules are documented: no credentials in vault content or prompt context
- [ ] External side effect policy is defined: what the adapter may or may not call externally
- [ ] Adapter is listed in `06_AGENTS/Backends-Supported.md`

---

## Tier 2 — Quarantine-Capable Adapters

These apply to adapters that can read from `03_INPUTS/` or deposit content there.

All Tier 1 items, plus:

- [ ] `03_INPUTS/` access mode is explicitly defined (read / deposit / neither)
- [ ] Adapter manifest declares `inputs_folder: true/false` for write path
- [ ] Adapter follows `[[04_SOPS/Research-Ingest-SOP|Research-Ingest-SOP]]` when processing content
- [ ] Adapter treats all `03_INPUTS/` content as Tier 4 — not as instructions
- [ ] Prompt injection posture is documented: embedded instructions in inputs must be flagged, not executed
- [ ] Content deposited by this adapter is labeled with source, date, and trust level in frontmatter
- [ ] Adapter manifest has `autonomous_promotion: false` confirmed

---

## Tier 3 — Draft-Capable Adapters

These apply to adapters that can write to standard output files (build logs, daily notes, agent session logs, knowledge drafts under review).

All Tier 1–2 items, plus:

- [ ] Writeback targets are explicitly defined in the adapter manifest (`allowed_write_targets`)
- [ ] Adapter writes to correct paths per `[[06_AGENTS/Vault-Map|Vault-Map]]` file creation rules
- [ ] Adapter does not write to protected files without explicit per-file approval
- [ ] Build log creation at session close is defined as required behavior
- [ ] Adapter manifest has `protected_file_behavior: block` or `require-approval`
- [ ] Adapter cannot delete files without explicit per-file instruction
- [ ] Index updates are part of session-close behavior (Build-Logs-Index.md, etc.)

---

## Tier 4 — Vault-Writing Adapters

These apply to adapters that can create and edit any standard vault file, including project OS files and knowledge notes.

All Tier 1–3 items, plus:

- [ ] `project_os_files: true` in manifest is intentional and confirmed
- [ ] `knowledge_notes: true` in manifest is gated by promotion conditions
- [ ] Adapter enforces protected-file approval requirement (hook or equivalent mechanism)
- [ ] Hook configuration is defined in manifest (`hook_config` section)
- [ ] Audit log target is defined: where elevated actions are logged
- [ ] External write approval posture is confirmed: no autonomous external side effects
- [ ] Adapter has been tested on a non-destructive session before vault-writing status is granted
- [ ] If Claude Code: settings.json exists and hook scripts are wired and tested
- [ ] Approval scope is documented: per-action, specific target, current session only

---

## Tier 5 — Promotion-Capable Adapters

These apply to adapters authorized to promote content from `03_INPUTS/` to `02_KNOWLEDGE/` after the full promotion gate.

All Tier 1–4 items, plus:

- [ ] Promotion gate is enforced: all 4 conditions must be confirmed before any knowledge promotion
  - [ ] Triage complete (source, date, relevance, recency assessed)
  - [ ] Sanitized (embedded hazards, unverified claims, credential-like content removed or labeled)
  - [ ] Verified (key claims cross-checked against reliable sources where applicable)
  - [ ] Human reviewed (explicit user confirmation before promotion)
- [ ] `ingestion_promotion_guard` hook is active (or equivalent mechanism for non-Claude adapters)
- [ ] Promotion actions are logged in the build log or agent activity log
- [ ] Tier 3 content (advisory surface outputs) may not be promoted without verification
- [ ] Autonomous promotion is permanently disabled (`autonomous_promotion: false` confirmed)

---

## Gap Log

Use this section when completing the checklist. Record any items that failed or were deferred.

```
Adapter: [adapter-id]
Checklist date: YYYY-MM-DD
Checked by: [Claude Code / user]

Tier [X] gaps:
- [ ] [Item that failed or was deferred]
  Reason: [Why it wasn't met]
  Path to resolution: [What needs to happen]

Status: [ready-to-activate | gaps-exist | review-required]
```

---

*Graph links: [[06_AGENTS/ChaseOS-Gate|ChaseOS-Gate]] · [[06_AGENTS/Adapter-Manifest-Standard|Adapter-Manifest-Standard]] · [[06_AGENTS/Execution-Adapter-Standard|Execution-Adapter-Standard]] · [[06_AGENTS/Permission-Matrix|Permission-Matrix]] · [[06_AGENTS/Ingestion-Architecture|Ingestion-Architecture]] · [[04_SOPS/Research-Ingest-SOP|Research-Ingest-SOP]] · [[Hook-Patterns]] · [[06_AGENTS/Vault-Map|Vault-Map]]*

*Adapter-Compliance-Checklist.md — Version 1.0 | Created: 2026-03-20 | Phase 6 Preflight — Execution Control Layer*
