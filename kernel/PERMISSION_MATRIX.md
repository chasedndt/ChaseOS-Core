---
type: kernel-permission-matrix
title: ChaseOS Kernel Permission Matrix
version: 1.0
created: 2026-05-11
source: Synthesized from 06_AGENTS/Permission-Matrix.md v1.3, Trust-Tiers.md v1.1, runtime/policy/, runtime/workflows/registry/, runtime/schedules/
---

# ChaseOS Kernel Permission Matrix

> Machine-state summary of all runtime permission assignments.
> Updated by the security-auditor agent after each permission audit.
> Canonical governance source remains: `06_AGENTS/Permission-Matrix.md`
> Trust tier definitions remain: `06_AGENTS/Trust-Tiers.md`
> This file is a read-optimized operational index — not the policy source.

---

## Section 1 — Runtime Roster and Trust Tiers

| Runtime | Identity | Trust Tier | Execution Surface | Status |
|---|---|---|---|---|
| Claude Code (Archon) | Archon-Runtime-Profile | Tier 2 | CLI / direct session | Active |
| OpenClaw | OpenClaw-Runtime-Profile | Tier 2 | Schedule executor + Discord ingress | Active |
| Hermes | Hermes-Runtime-Profile | Tier 2 (bounded shadow) | Bus watch + review + synthesis | Active |
| Claude Chat (claude.ai) | Advisory surface | Tier 3 | Chat — advisory only | Active |
| OpenAI Chat | Advisory surface | Tier 3 | Chat — advisory only | Advisory |
| NotebookLM | Advisory surface | Tier 3 | Upload + synthesis | Advisory |
| Perplexity | Advisory surface | Tier 3 | Research connector / API | Active (via connector) |
| Grok / xAI | Advisory surface | Tier 3 | Research connector / API | Active (via connector) |
| n8n (planned) | — | Tier 2 (conditional) | Workflow runtime | Planned |
| External inputs / Tier 4 content | Untrusted | Tier 4 | 03_INPUTS/ quarantine | Always present |

---

## Section 2 — Read Permissions by Runtime

| Runtime | Vault Files | Protected Files | Raw Inputs (03_INPUTS/) | External |
|---|---|---|---|---|
| Archon (Claude Code) | ✅ All | ✅ Read-only | ✅ Data only | ⚠️ Explicit scope |
| OpenClaw | ⚠️ Workflow-declared only | ❌ | ❌ | ⚠️ Connector-only |
| Hermes | ⚠️ Manifest-declared only | ❌ | ❌ in current shadow | ❌ in current shadow |
| Claude Chat | ✅ User-provided only | ✅ User-provided only | ✅ Data only | ⚠️ Web search if enabled |
| Perplexity | ❌ (no vault) | ❌ | ❌ | ✅ Web search |
| Grok | ❌ (no vault) | ❌ | ❌ | ✅ X / web |
| MCP Server | ✅ Curated endpoints only | ❌ | ❌ | ❌ |

---

## Section 3 — Write Permissions by Runtime

| Runtime | Standard Logs | Operator Briefs | Acquisition Packs | Canonical Knowledge | Protected Files | Vault-Wide |
|---|---|---|---|---|---|---|
| Archon (Claude Code) | ✅ | ✅ | ✅ | ✅ (with direction) | ⚠️ Per-file approval | ⚠️ With direction |
| OpenClaw | ✅ (declared targets) | ✅ | ✅ | ❌ | ❌ | ❌ |
| Hermes | ✅ (Agent-Activity only) | ✅ (drafts only) | ❌ | ❌ | ❌ | ❌ |
| MCP Server | ✅ (AOR-bounded) | ✅ (operator_today/close_day only) | ❌ | ❌ | ❌ | ❌ |
| All advisory surfaces | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |

---

## Section 4 — External Connector Permissions

| Connector | Runtime(s) | Credential | Approval Required | Active |
|---|---|---|---|---|
| RSS feeds | OpenClaw (acquisition_workflow) | None | None declared | Yes |
| Web scrape (urllib) | OpenClaw (acquisition_workflow) | None | None declared | Yes |
| Perplexity API | OpenClaw (acquisition_workflow) | PERPLEXITY_API_KEY (env) | **None — RISK C-1** | If key set |
| Grok / xAI API | OpenClaw (acquisition_workflow) | XAI_API_KEY (env) | **None — RISK C-1** | If key set |
| Email IMAP | OpenClaw (acquisition_workflow) | IMAP credentials (env) | **None — RISK M-2** | If key set |
| Google Docs/Drive | OpenClaw (acquisition_workflow) | GOOGLE_OAUTH_TOKEN (env) | **None — RISK M-2** | If key set |
| Anthropic API (LLM synthesis) | Hermes (hermes_review_execute) | ANTHROPIC_API_KEY (env) | **Fail-open on key presence — RISK C-2** | If key set |
| Discord webhook | OpenClaw (sbp_example_digest) | example-project_DISCORD_WEBHOOK_URL (env) | None declared | If enabled + key set |
| Whop API | OpenClaw (SBP pipelines) | WHOP_API_KEY (env) | None declared | Not yet active |
| n8n webhook / MCP | n8n executor | N8N_BASE_URL + N8N_API_KEY (env) | Policy gate (enabled + secrets_configured) | If configured |
| Browser navigation | Archon / browser_research | None | Bounded URL allowlist | Allowed |
| Host process control | OpenClaw / lifecycle | None | Bounded local process only | Allowed |
| Host startup folder | OpenClaw / lifecycle | None | **No per-action approval — RISK H-2** | Allowed |

---

## Section 5 — Schedule Permission Map

| Schedule | Enabled | Permission Class | External Side Effects | Approval |
|---|---|---|---|---|
| sch-operator-today-0700 | true | no_protected_file_writes | None | none |
| sch-operator-close-day-1900 | true | no_protected_file_writes | None | none |
| **sch-os-hygiene-graph-0300** | **true** | **vault_graph_write** | Vault mutations | none |
| **sch-acquisition_workflow-0550** | **true** | acquisition_pack_only | **Live API calls (paid)** | **none — RISK C-1** |
| sch-sbp_example_digest-0600 | false | no_protected_file_writes | Discord delivery (if enabled) | none (RISK C-3 when enabled) |
| sch-archon-watch-every-minute | false | bus_result_only | None | none |
| sch-hermes-watch-every-minute | false | bus_result_only | Anthropic API (RISK C-2) | none |
| sch-openclaw-watch-every-minute | false | no_protected_file_writes | None | none |

---

## Section 6 — Workflow Permission Ceilings

| Workflow | Permission Ceiling | Task Type | Write Scope | External? |
|---|---|---|---|---|
| operator_today | no_protected_file_writes | operator-briefing | docs/framework-logs/Operator-Briefs/ | No |
| operator_close_day | no_protected_file_writes | operator-briefing | docs/framework-logs/Operator-Briefs/ | No |
| os_hygiene_graph | vault_graph_write | os-graph-maintenance | Maintain-Runs/, Hygiene-Reports/, Daily/ | No |
| acquisition_workflow | acquisition_pack_only | source-pack-builder | runtime/acquisition/packs/ | **Yes — paid APIs** |
| sbp_example_digest | no_protected_file_writes | scheduled-briefing | docs/framework-logs/SBP-Runs/ | Yes — Discord |
| graph_hygiene | proposal_log_only | graph-hygiene | Hygiene-Reports/ | No |
| graduate_ideas | proposal_log_only | idea-graduation | Graduation-Proposals/ | No |
| hermes_watch | bus_result_only | coordination | docs/framework-logs/Agent-Activity/ | Anthropic API |
| archon_watch | bus_result_only | coordination | docs/framework-logs/Agent-Activity/ | No |
| openclaw_watch | no_protected_file_writes | coordination | Operator-Briefs/, Agent-Activity/ | No |
| hermes_review_execute | bus_result_only | review | docs/framework-logs/Agent-Activity/ | Anthropic API |
| hermes_research_synthesis | quarantine_and_logs_only | research-synthesis | 03_INPUTS/quarantine/digest/ | No |
| browser_research | quarantine_and_logs_only | browser-research | 03_INPUTS/quarantine/, Operator-Briefs/ | Yes — URL fetch |
| sbp_example_digest (whop) | no_protected_file_writes | scheduled-briefing | docs/framework-logs/SBP-Runs/ | Yes — Whop API |
| agent_runtime_governance_audit | proposal_log_only | MissionOps-runtime-audit | docs/framework-logs/Workflow-Proofs/ | No |
| openclaw_post_review_task | bus_result_only | review | docs/framework-logs/Agent-Activity/ | No |
| source_pack_builder | acquisition_pack_only | source-pack-builder | runtime/acquisition/packs/ | No |
| trace_idea | report_only | trace-idea | docs/framework-logs/Trace-Reports/ | No |
| drift_scan | report_only | drift-scan | docs/framework-logs/Drift-Reports/ | No |
| meeting_ingest_linker | report_only | meeting-ingest | docs/framework-logs/Link-Proposals/ | No |
| hermes_skill_review | proposal_log_only | idea-graduation | Quarantine scan (report only) | No |

---

## Section 7 — Deleted / Never-Permitted Actions

| Action | Ruling | Applies To |
|---|---|---|
| Delete vault files | ⚠️ Explicit per-file instruction | Archon (Claude Code) only; ❌ all other runtimes |
| Edit protected files | ⚠️ Explicit per-file approval per session | Archon only; ❌ all others |
| Promote to 02_KNOWLEDGE/ (Hermes/OpenClaw) | ❌ Never autonomous | Hermes, OpenClaw, all automated runtimes |
| Execute shell/scripts (Hermes) | ❌ Never | Hermes |
| Multi-repo access | ❌ Disabled by default | All runtimes |
| Gateway input as instructions (Discord, email, RSS) | ❌ Never | All runtimes — Tier 4 data only |
| Run without audit log | ❌ Never | All runtimes |
| Undeclared file reads (Hermes) | ❌ Never — halt + escalation | Hermes |
| Auto-promote quarantine skills | ❌ Never without operator review | Hermes |
| Commit / push to git repo | ❌ Not documented — treat as ⚠️ explicit instruction per op | Archon; ❌ all automated runtimes |
| Force-push to main | ❌ Prohibited | All runtimes |
| Approval self-escalation | ❌ Never | All runtimes |

---

## Section 8 — Gate Hook Configuration

| Hook | Event | File | Status |
|---|---|---|---|
| protected_write_guard.py | PreToolUse (Write/Edit) | runtime/policy/protected_files.yaml | Active — 13 files |
| ingestion_promotion_guard.py | PreToolUse (Write) | Checks 03_INPUTS/ promotion paths | Active |
| session_start_context.py | UserPromptSubmit | — | Active |
| session_end_audit.py | Stop | — | Active |

**Known drift:** `protected_files.yaml` last synced 2026-03-20. Permission-Matrix.md updated 2026-04-25. Requires re-verification. (RISK M-1)

---

## Section 9 — Open Audit Items

Items from security/permission-audit-2026-05-11.md requiring resolution:

| Item | Risk Level | Owner | Status |
|---|---|---|---|
| acquisition_workflow: add approval gate or shadow_mode | CRITICAL | Operator | Open |
| hermes_watch: change LLM synthesis to explicit-enable | CRITICAL | Operator | Open |
| sbp_example_digest: add draft-review before Discord enable | CRITICAL | Operator | Open |
| setup_init write target: narrow from vault-wide | HIGH | Operator | Open |
| host.startup_folder: add per-action approval | HIGH | Operator | Open |
| Watch loop rate limiting before enabling | HIGH | Operator | Open |
| os_hygiene_graph: add pre-mutation snapshot / per-file diff log | HIGH | Operator | Open |
| protected_files.yaml sync verification | MEDIUM | Operator | Open |
| Email IMAP + Google adapters: disable or scope in acquisition | MEDIUM | Operator | Open |
| Shadow workflows: archive or deprecate | MEDIUM | Operator | Open |
| Add git operations row to Permission-Matrix.md | MEDIUM | Operator | Open |

---

*kernel/PERMISSION_MATRIX.md — Operational permission index | Version 1.0 | Created 2026-05-11 | Source: security/permission-audit-2026-05-11.md + 06_AGENTS/Permission-Matrix.md v1.3 | Next audit: after any new workflow, connector, or schedule is added*

---
*Graph links: [[Permission-Matrix]] · [[Trust-Tiers]] · [[Vault-Map]] · [[Agent-Registry]] · [[OpenClaw-Runtime-Profile]] · [[Hermes-Runtime-Profile]] · [[Codex-Runtime-Profile]] · [[ChaseOS-Vault-Maintenance]]*
