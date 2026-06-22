---
type: sop
title: Promotion Session SOP
version: 1.0
created: 2026-03-21
scope: Anthropic Agent Harness (Claude Code) — gated promotion writes
---

# Promotion Session SOP — Standard Operating Procedure

> This SOP defines how to run a promotion pass in the Claude Code lane.
> A promotion pass is any session where processed notes from `03_INPUTS/` are promoted to `02_KNOWLEDGE/`.
> The `ingestion_promotion_guard` hook requires an explicit env var to allow these writes.
> This SOP tells you when to set it, what preconditions must be met, and what to do after.

---

## What This SOP Governs

Any session where Claude Code will write to `02_KNOWLEDGE/` or promote raw input content to the knowledge layer. Applies to:

- Source-note promotion (transcript, article, document → `02_KNOWLEDGE/`)
- Synthesis-note promotion (digest, NotebookLM output, research bundle → `02_KNOWLEDGE/`)
- Generated-idea creation or promotion
- Any write to `02_KNOWLEDGE/` that was sourced from `03_INPUTS/`

Does **not** apply to:
- Documentation passes (framework docs, SOPs, templates)
- Project-OS updates
- Build logs or archive notes
- Edits to existing knowledge notes that were already promoted

---

## Promotion Gate Summary

The `ingestion_promotion_guard.py` hook blocks all writes to `02_KNOWLEDGE/` by default.

**To allow promotion writes:** set `CHASEOS_PROMOTION_APPROVED=1` in the shell before launching Claude Code.

**Why the gate exists:** Raw input content is Tier 3–4 (untrusted). The gate ensures promotion never happens silently or automatically — every promotion session is a human-authorized act.

---

## Preconditions

Before setting the env var and starting a promotion session, confirm:

| Precondition | Check |
|--------------|-------|
| Raw input file exists in `03_INPUTS/` | File is present with correct frontmatter |
| Input has been triaged | `injection_scan: clean` in frontmatter; source identified |
| Input is not flagged for injection | No suspicious embedded instructions noted during review |
| Content has been assessed for trust level | `trust_level` set in frontmatter (tier-3 or tier-4 most common) |
| You know the promotion target | `02_KNOWLEDGE/[Domain]/[topic].md` path is clear |
| You know the knowledge class | `knowledge_class` field selected from taxonomy (see `[[06_AGENTS/Knowledge-Taxonomy|Knowledge-Taxonomy]]`) |
| Partial vs. full promotion decided | If content has unverified claims: partial promotion with `[UNVERIFIED]` labels and `verified_status: partially-verified` |

If any precondition is unclear, triage first. Do not start the promotion session until triage is complete.

---

## Session Setup

**Step 1 — Open a terminal in the vault root.**

**Step 2 — Set the promotion approval env var:**

```bash
export CHASEOS_PROMOTION_APPROVED=1
```

This must be set in the same shell session where Claude Code is launched. It authorizes promotion writes for this session only — it does not persist across sessions.

**Step 3 — Verify the hook will pass (optional but recommended for new operators):**

If you want to confirm the env var is recognized before starting:
```bash
echo $CHASEOS_PROMOTION_APPROVED
```
Expected output: `1`

**Step 4 — Launch Claude Code from that shell:**

```bash
claude
```

Claude Code inherits the env var from the shell. The promotion guard will allow writes to `02_KNOWLEDGE/` for this session.

---

## During the Session

1. **State the session intent explicitly** at the start of the prompt: e.g., "This is a promotion pass for `03_INPUTS/Transcript-Raw/[file].md`. Promote it to `02_KNOWLEDGE/[Domain]/[topic].md`."

2. **Follow `Research-Ingest-SOP.md`** for per-type processing rules (digest, transcript, NotebookLM, source).

3. **Apply taxonomy frontmatter** to every promoted note:
   - `knowledge_class` — required (see `[[06_AGENTS/Knowledge-Taxonomy|Knowledge-Taxonomy]]` for six classes)
   - `trust_tier` — required
   - `linked_index` — required (link to the domain index file)
   - `action_status` — required (`open` if actions extracted, `none` if no actions)
   - `verified_status` — required (`verified`, `partially-verified`, or `unverified`)

4. **Apply the partial promotion pattern** if the input mixes verified and unverified content:
   - Promote structural/conceptual layer as normal
   - Label unverified specifics with `[UNVERIFIED]`
   - Set `verified_status: partially-verified`
   - Note what is verified vs. unverified in a "Verification Status" section

5. **Update the domain index** after promotion:
   - Add a link to the new note in `02_KNOWLEDGE/[Domain]/[Domain-Index].md`
   - Use the format established in the existing index

6. **Update the raw input file** after promotion:
   - Update/add `promoted_to` in frontmatter
   - Update status to `processed`
   - Add or confirm the PROCESSED banner

---

## Action Extraction Close-out

After all notes are promoted, before closing the session:

1. List all action items extracted across all notes promoted in this session
2. Present them explicitly: "Here are the extracted actions — which should be routed?"
3. For each action item, get user routing decision:
   - Route to `docs/framework-home/Now.md` open actions
   - Route to `01_PROJECTS/[Project]/[Project]-OS.md` open loops
   - Leave note-local (`action_status: open` in note frontmatter)
   - Queue follow-up research into `03_INPUTS/`
   - Reject (no action needed)
4. Update `action_status` in each note based on routing decisions

---

## Required Outputs

Every promotion session must produce:

| Output | Location |
|--------|---------|
| Promoted knowledge note(s) | `02_KNOWLEDGE/[Domain]/[topic].md` |
| Domain index updated | `02_KNOWLEDGE/[Domain]/[Domain-Index].md` |
| Raw input file updated | `03_INPUTS/[subfolder]/[file].md` — status + PROCESSED banner |
| Build log | `docs/framework-logs/Build-Logs/YYYY-MM-DD-ChaseOS-[descriptor].md` |
| Agent activity log | `docs/framework-logs/Agent-Activity/YYYY-MM-DD-claude-[descriptor].md` |

Archive note is optional for routine promotion sessions. Create one only for major passes (multiple notes, new patterns established, or framework changes made).

---

## Session Close-out Checklist

Before ending a promotion session:

- [ ] All target files written to `02_KNOWLEDGE/`?
- [ ] Domain index(es) updated?
- [ ] Raw input file(s) updated with `promoted_to` + PROCESSED banner?
- [ ] Taxonomy frontmatter applied to all promoted notes?
- [ ] Action extraction close-out completed (actions presented to user, routing decided)?
- [ ] `action_status` updated in note frontmatter after routing decisions?
- [ ] Build log written to `docs/framework-logs/Build-Logs/`?
- [ ] Agent activity log written to `docs/framework-logs/Agent-Activity/`?
- [ ] Both index files updated (Build-Logs-Index.md, Agent-Activity-Index.md)?

If yes to all — session is clean. If any are no, complete before closing.

---

## Proposed Helper Tooling Boundary

When it becomes useful, a lightweight shell wrapper for promotion sessions would eliminate the manual env-var step. The boundary for that tooling:

**What the helper should do (automation-safe):**
- Set `CHASEOS_PROMOTION_APPROVED=1`
- Optionally: accept a target file path as an argument
- Launch Claude Code in the vault root

**What the helper must NOT do:**
- Auto-select content to promote (human decision only)
- Auto-run without confirmation prompt
- Bypass the hook by modifying `.claude/settings.json`
- Set the env var system-wide or permanently

**Recommended implementation when needed:** A simple shell script (`promote.sh` or `chase-promote`) that prompts for confirmation, sets the env var, and launches `claude`. No more than 10–15 lines. Trivial to audit.

---

## Failure Handling

| Situation | Response |
|-----------|----------|
| Hook blocks the write despite env var set | Check that env var is in the correct shell (same process); restart Claude Code from that shell |
| Claude attempts a promotion without the env var set | This is correct hook behavior — set the var and retry |
| Unresolved injection suspicion in raw input | Stop promotion; flag to user; do not promote until resolved |
| Promoted note has unresolved verification gaps | Use partial promotion pattern; do not block promotion |
| Domain index file not found | Check `Vault-Map.md` for correct path; create index if genuinely missing |

---

## Related

| Document | Purpose |
|----------|---------|
| `[[Research-Ingest-SOP]]` | Per-type processing rules for each content class |
| `[[Ingestion-Cadence]]` | When to run promotion sessions; daily/weekly rhythm |
| `[[06_AGENTS/Knowledge-Taxonomy|Knowledge-Taxonomy]]` | Six knowledge classes; taxonomy frontmatter schema |
| `[[Untrusted-Input-Handling-SOP]]` | Full five-stage flow; injection handling |
| `[[06_AGENTS/ChaseOS-Gate|ChaseOS-Gate]]` | Full enforcement layer documentation |
| `[[Hook-Patterns]]` | Hook behavior reference; ingestion_promotion_guard mechanics |
| `03_INPUTS/03_INPUTS-Folder-Guide.md` | Subfolder conventions; naming; input states |

---

*Graph links: [[06_AGENTS/Vault-Map|Vault-Map]] · [[Research-Ingest-SOP]] · [[Ingestion-Cadence]] · [[06_AGENTS/Knowledge-Taxonomy|Knowledge-Taxonomy]] · [[Untrusted-Input-Handling-SOP]] · [[06_AGENTS/ChaseOS-Gate|ChaseOS-Gate]] · [[Hook-Patterns]] · [[06_AGENTS/Ingestion-Architecture|Ingestion-Architecture]]*

*Promotion-Session-SOP.md — Version 1.0 | Created: 2026-03-21 | Phase 6D — Operational Readiness*
