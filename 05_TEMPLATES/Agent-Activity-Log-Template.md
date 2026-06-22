---
type: template
title: Agent Activity Log Template
version: 1.1
created: 2026-03-21
updated: 2026-04-24
scope: framework-level
---

# Agent Activity Log Template

> Template for audit-significant or elevated non-build agent activity logs in `docs/framework-logs/Agent-Activity/`.
> Use for: elevated actions, hook-blocked events, automated workflow runs, audit events, or policy-significant runtime records.
> For broader runtime-side operational records, `Agent-Session-Log-Template.md` may be used as the current base template.
> For engineering/docs/architecture sessions, use the build log in `docs/framework-logs/Build-Logs/` instead.
> See `docs/framework-logs/Agent-Activity/Agent-Activity-Folder-Guide.md` for what belongs here vs build logs.

---

## Naming Convention

```
docs/framework-logs/Agent-Activity/YYYY-MM-DD-[agent]-[descriptor].md
```

Examples:
```
2026-03-21-claude-protected-write-audit.md
2026-03-21-n8n-daily-digest-ingest.md
2026-03-22-claude-hook-verification.md
```

---

## Template

```markdown
---
type: agent-activity-log
date: YYYY-MM-DD
agent: [hermes | claude-harness | n8n | local-oss | other]
runtime_node: [[Hermes-Runtime-Profile]] or [runtime profile node / N/A]
trigger: [manual | scheduled | webhook | hook-event]
session_type: [verification | elevated-action | automated-workflow | audit-event]
---

# Agent Activity Log — [Descriptor]

**Date:** YYYY-MM-DD
**Agent / Adapter:** [Which adapter produced this action]
**Runtime node:** [[Hermes-Runtime-Profile]] or [runtime profile node / N/A]
**Trigger type:** [Scheduled / webhook / hook-event / manual]
**Execution timestamp:** [HH:MM UTC or local]
**Session type:** [What kind of activity this captures]

---

## Actions Taken

<!-- List every specific write, call, or operation. Be precise about paths and targets. -->

| Action | Target / Details | Status |
|--------|-----------------|--------|
| [Write / Edit / Delete / API call / hook-block] | [full path or endpoint] | [completed / blocked / failed] |

---

## Write Targets

<!-- Full paths of any files written or modified -->

- [none] or list each file

---

## External Calls

<!-- External APIs or services contacted, if any -->

- [none] or describe each call with target and purpose

---

## Errors or Blocks

<!-- Any failed actions, hook blocks, or partial states -->

- [none] or describe each error with exit code and stderr output

---

## Approval Record

<!-- Required if any elevated action was taken -->

| Action | Approved by | Approval form | Session context |
|--------|-------------|--------------|----------------|
| [Protected-file edit / deletion / external write] | [Vault owner / explicit instruction] | [Verbal in session / env var / explicit instruction] | [Brief context] |

---

## Notes

<!-- Any other relevant context for this activity record -->

---

*Agent activity log — see `docs/framework-logs/Agent-Activity/Agent-Activity-Folder-Guide.md` for folder conventions.*
*Gate: `[[06_AGENTS/ChaseOS-Gate|ChaseOS-Gate]]` · Security model: `[[06_AGENTS/Agent-Security-Model|Agent-Security-Model]]` · Build logs: `[[Build-Logs-Index]]`*
```

---

## Field Definitions

| Field | Required | Notes |
|-------|----------|-------|
| Agent / adapter | Yes | Which adapter produced this action |
| Runtime node | Yes when runtime-specific | Obsidian graph node for the runtime instance, e.g. `[[Hermes-Runtime-Profile]]` for Hermes |
| Trigger type | Yes | Scheduled / webhook / hook-event / manual |
| Execution timestamp | Yes | When the action started |
| Actions taken | Yes | List of specific writes, calls, or operations |
| Write targets | Yes | Full paths of any files written or modified |
| External calls | If any | External APIs or services contacted |
| Errors or blocks | If any | Failed actions, hook blocks, partial states |
| Approval record | If elevated | Who approved; which action; session context |

---

## When to Create an Activity Log vs a Build Log

| Situation | Log type |
|-----------|---------|
| Engineering or documentation build session | Build log in `docs/framework-logs/Build-Logs/` |
| Elevated action audit (protected-file edit, deletion) | Activity log here |
| Hook-blocked write event | Activity log here |
| Automated workflow execution (n8n) | Activity log here |
| Protected-file edit that occurs within a build session | Note in build log (not a separate activity log) |
| Runtime-side operational bind that is not especially audit-heavy | `Agent-Session-Log-Template.md` may be sufficient |

*Agent-Activity-Log-Template.md — Version 1.1 | Created: 2026-03-21 | Updated: 2026-04-24*



## Related
- [[Projects-Hub]]


*Graph links: [[06_AGENTS/Vault-Map|Vault-Map]] · [[Hermes-Runtime-Profile]] · [[Agent-Activity-Index]]*
