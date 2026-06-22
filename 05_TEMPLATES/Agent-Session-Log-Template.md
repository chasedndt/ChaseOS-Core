---
type: template
title: Agent Session Log
agent-primary: true
usage: Current base template for runtime-side operational records in docs/framework-logs/Agent-Activity/. Not for primary build/dev/docs session history.
updated: 2026-04-24
---

# Agent Session Log — {{DATE}}

> **Agent-first template.** The agent/system creates this entry.
> Manual user fill is fallback when agent logging is unavailable.
> File at: `docs/framework-logs/Agent-Activity/YYYY-MM-DD-[runtime-or-agent-slug]-[descriptor].md`
> Hermes records must use `hermes` or `hermes-optimus` in the filename slug and link `[[Hermes-Runtime-Profile]]`.
> Use for runtime activity, operational binds, automation traces, or non-build agent records.
> For engineering, documentation, and architecture sessions, use `docs/framework-logs/Build-Logs/` instead.

---

## Session Metadata

**Date:** {{DATE}}
**Agent / System:** {{Hermes / Claude Code / Claude Chat / n8n / OpenRouter / other}}
**Runtime node:** {{[[Hermes-Runtime-Profile]] / runtime profile node / N/A}}
**Session type:** {{Runtime bind / Audit-support / Automation trace / Context update / Data processing / Review / Other non-build}}
**Triggered by:** {{User prompt / Scheduled workflow / Automated trigger / Hook or policy event}}
**Duration (approx):** {{estimate or N/A}}

---

## Task Summary

**What was requested or triggered:**
{{Brief description of the runtime-side task, workflow, or operational event that initiated this record}}

**Scope:**
{{What was in scope for this session}}

**What was NOT in scope:**
{{Any explicit exclusions or deferred work}}

---

## Inputs Used

| Input | Type | Location |
|-------|------|---------|
| {{file or context}} | {{Project-OS / SOP / Knowledge / External}} | {{path or source}} |

---

## Actions Taken

1. {{Action 1}}
2. {{Action 2}}
3. {{Action 3}}

---

## Outputs Produced

| Output | Type | Written to |
|--------|------|-----------|
| {{output name}} | {{Log / Note / Edit / Template fill}} | {{path}} |

---

## Open Loops / Flags

- [ ] {{Any incomplete items, flags for user review, or follow-up needed}}

---

## Agent Notes

{{Any observations, warnings, anomalies, runtime posture notes, or decisions made during the session that should be retained}}

---

## Routing Reminder

- Use this template for Agent Activity records in `docs/framework-logs/Agent-Activity/`.
- Use Build Logs for primary engineering, documentation, or architecture session history.
- Use `Agent-Activity-Log-Template.md` when the record is specifically security/audit/elevated-action focused.

---

*Graph links: [[06_AGENTS/Vault-Map|Vault-Map]] · [[06_AGENTS/Agent-Output-Conventions|Agent-Output-Conventions]] · [[Agent-Activity-Index]] · [[Hermes-Runtime-Profile]] · [[HERMES]] · [[Build-Logs-Index]] · [[Agent-Activity-Log-Template]]*

*Session log created: {{DATE}} | Agent: {{agent}} | Template: Agent-Session-Log-Template.md*
