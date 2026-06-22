---
title: Full-System Operator Safety SOP
type: sop
status: active
version: 1.0
created: 2026-04-15
updated: 2026-04-15
phase: Phase 9 — Full-System Operator Surface sub-track
knowledge_class: canonical-state
---

# Full-System Operator Safety SOP
## ChaseOS — Safety Protocol for All FSOS Executions

> This SOP defines the safety protocol that governs all Full-System Operator Surface executions across browser, terminal, desktop, and filesystem surfaces. It covers scope declaration, approval escalation, credential boundaries, high-risk action handling, surface-switch rules, stop conditions, failure escalation, replay/audit expectations, and operator intervention. Every FSOS run must conform to this SOP before it is considered governed.

---

## 1. Purpose

ChaseOS FSOS executions take real-world computer actions — navigating browsers, executing terminal commands, interacting with desktop UI, and operating the filesystem. These actions can have effects that are:
- **Irreversible** (form submissions, file deletions, sent messages)
- **High-blast-radius** (terminal commands that modify system state)
- **Privacy-sensitive** (actions involving credentials, personal data, or protected files)
- **Injection-vulnerable** (content encountered on external surfaces may contain hostile instructions)

This SOP defines the non-negotiable safety floor for all FSOS executions. It is enforced by the FSOS runtime contracts, validated by the AOR policy engine, and audited by every run's `OperatorRunAudit` artifact.

---

## 2. Safe Scope Declaration

**Before any FSOS run begins, the workflow manifest must declare:**

- `target_uris` — explicit list of URLs, paths, or process names the run may act on
- `allowed_origins` — for browser runs: allowed domains (no wildcards without approval)
- `allowed_paths` — for filesystem runs: allowed path prefixes (no root paths without Tier 1 grant)
- `forbidden_zones` — explicit exclusion list; vault protected-file paths must always be in forbidden_zones for non-vault workflows
- `max_actions` — hard ceiling on total action count (default: 50 for research; 20 for write-capable runs)
- `max_duration_seconds` — hard time ceiling (default: 300 seconds for interactive; 1800 for long-running)
- `requires_approval` — list of action classes requiring approval gate before execution
- `external_network` — boolean; false unless network access is required
- `credential_access` — boolean; always false unless Tier 1 explicit grant

**Scope validation is performed by AOR before dispatch.** A manifest that declares scope outside the workflow's registered permission ceiling will be rejected before execution begins.

---

## 3. Approval Escalation Rules

The following actions ALWAYS require an approval gate, regardless of what the workflow manifest declares. These are absolute requirements — they cannot be declared as non-approval-required.

| Action | Surface | Reason |
|--------|---------|--------|
| Form submission (POST) | Browser | Irreversible server-side state change |
| File deletion | Filesystem | Irreversible; high blast radius |
| Credential/password field input | Browser, Desktop | Credential exposure risk |
| Navigation outside allowed_origins | Browser | Scope drift |
| Terminal destructive commands (`rm`, `drop`, `delete`, `format`) | Terminal | Irreversible |
| Download file trigger | Browser | Unscoped filesystem write |
| Cross-repo file copy/move | Filesystem | Unintended data movement |
| Any action on a vault protected file | All | Gate rules — absolute |

**Approval responses are recorded in the audit trail before execution continues.** An approved action and a denied action both appear in the audit with operator identity and timestamp.

---

## 4. Secret / Credential Boundaries

These rules are absolute and may not be overridden by any manifest, workflow, or operator instruction:

1. **No FSOS run reads, logs, or transmits credential values.** This includes API keys, passwords, OAuth tokens, environment variable values, and keychain entries.
2. **No FSOS browser session fills credential/password fields without explicit operator approval at that step.** The approval description must include the target domain and field description.
3. **No FSOS terminal command reads `.env` files, credential stores, or `~/.ssh/` contents.** These paths are in the default `forbidden_zones` for terminal adapters.
4. **Credential detection is active.** If the FSOS executor detects what appears to be a credential value in page content, terminal output, or file content — it redacts it from the audit artifact and flags it to the operator.
5. **The `PERPLEXITY_API_KEY`, `XAI_API_KEY`, `ANTHROPIC_API_KEY`, and all other Phase 8+ connector keys are out of scope for FSOS.** These are accessed by capture connectors through their own env-var-only discipline. FSOS does not share credential access with capture connectors.

See `04_SOPS/Credential-Boundaries-SOP.md` for the full credential handling policy.

---

## 5. High-Risk Action Handling

High-risk actions are classified by potential for irreversibility and blast radius:

| Risk Level | Definition | Handling |
|------------|------------|---------|
| **CRITICAL** | Irreversible + high blast radius (file deletion, form submit, terminal destructive) | Always approval gate; logged in audit with full context |
| **HIGH** | Irreversible + limited blast radius (send message, create record, download file) | Always approval gate; logged |
| **MEDIUM** | Reversible + some side effects (navigate to new domain, open new window) | Approval if outside allowed_origins; logged |
| **LOW** | Reversible + no meaningful side effects (scroll, screenshot, extract text) | No approval gate required; logged |

Risk levels are declared in the workflow manifest for each step class. AOR validates risk level assignments against the action type taxonomy before dispatch. A step that declares LOW risk but is of action type `form_submit` will be rejected — the action type overrides the declared risk level.

---

## 6. Surface-Switch Rules

When a workflow requires switching between surfaces (future multi-surface workflows):

1. Each surface switch requires a separate scope declaration in the manifest
2. A surface switch emits a `STEP_COMPLETE` event for the outgoing surface and a new `PLAN_READY` event for the incoming surface
3. Data passed between surfaces is treated as Tier 3 (research-grade) — not as trusted instructions
4. If the incoming surface requires higher permissions than the workflow's trust ceiling, the switch is blocked
5. Surface switches are audited: each surface's events are labeled with the surface name

---

## 7. Stop Conditions

FSOS execution must halt immediately on any of the following:

| Stop Condition | Response |
|---------------|---------|
| `max_actions` reached | Emit `SESSION_FAILED(reason=ACTION_LIMIT_REACHED)`; write audit; notify operator |
| `max_duration_seconds` reached | Emit `SESSION_FAILED(reason=TIME_LIMIT_REACHED)`; write audit; notify operator |
| Navigation to forbidden_zone | Block action; emit `STEP_FAILED(reason=FORBIDDEN_ZONE)`; halt |
| Credential value detected in input field | Block action; emit `AWAIT_APPROVAL` with warning; do not proceed without operator acknowledgment |
| Prompt injection detected in external content | Block; flag to operator; do not process content as instruction |
| AOR scope validation failure | Reject run before it starts; return error to shell |
| Operator DENY response | Halt immediately; write `outcome=DENIED` in audit |
| Unhandled exception in adapter | Enter recovery mode; if unrecoverable: halt and write audit |
| Vault protected-file operation attempted | Block; emit `STEP_FAILED(reason=PROTECTED_FILE_WRITE)`; halt |

Stop conditions are enforced by the executor, not the adapter. The adapter cannot override stop conditions.

---

## 8. Failure Escalation

When FSOS execution fails:

1. **Step failure (recoverable):**
   - Emit `STEP_FAILED`
   - Enter recovery mode
   - Take recovery screenshot (browser) or record state (terminal/desktop/filesystem)
   - Emit `RECOVERY_STARTED`
   - Attempt cleanup
   - If recovery succeeds: emit `RECOVERY_COMPLETE`; continue plan (or halt per manifest)
   - If recovery fails: escalate to session failure

2. **Session failure (unrecoverable):**
   - Emit `SESSION_FAILED`
   - Write partial `OperatorRunAudit` with failure context
   - Write failure log to `docs/framework-logs/Agent-Activity/`
   - Notify operator via configured delivery adapter (if any)
   - Leave vault in pre-run state (no partial writes remain)
   - Return run result to AOR for recording in Decision Ledger (if failure pattern is novel)

3. **Repeated failure pattern:**
   - If the same step type fails across multiple runs: flag pattern to operator
   - Record in Execution Repair Memory (see `06_AGENTS/Agent-Memory-Architecture.md`)
   - Operator reviews and decides whether to adjust manifest, adapter, or defer workflow

---

## 9. Replay / Audit Expectations

Every FSOS run produces an `OperatorRunAudit` artifact. The artifact must contain:

- Full event sequence (every `OperatorEvent` from `PLAN_READY` to final event)
- Scope declaration (exactly what was declared before execution)
- All approval records (what was asked, what was answered, by whom, when)
- Recovery records (what failed, what was attempted, what resulted)
- Vault writes (if any — paths and capture IDs)
- Outcome: `COMPLETE | FAILED | DENIED | HALTED`
- Duration and step counts

**Replay:** Given a `run_id`, the `replay.py` module can reconstruct the full event sequence from the audit artifact. Replay is read-only — it does not re-execute. It is for post-mortem analysis and operator review.

Audit artifacts are stored at: `docs/framework-logs/Agent-Activity/YYYY-MM-DD_HHMMSS_operator_{surface}_{run_id}.json`

Future: formal `runtime/audit/` directory when audit subsystem is built.

---

## 10. Operator Intervention Expectations

Operators should expect to be involved at these points:

| When | What operator does |
|------|--------------------|
| Before run starts | Review workflow manifest scope; approve or reject run |
| At approval gates | Respond APPROVE/DENY at each approval-required step |
| At ASK mode | Provide clarification the run needs to proceed |
| On FAILED outcome | Review audit; decide whether to retry, adjust manifest, or escalate |
| On DONE outcome | Review extracted content; decide whether to promote from quarantine |

Operators should NOT need to intervene:
- During LOW and MEDIUM risk steps (these are pre-authorized by manifest)
- During recovery from transient failures (recovery is automatic)
- For vault write decisions for log files (these follow the standard AOR writeback chain)

---

## 11. Checklist — Before Any FSOS Workflow Is Enabled

- [ ] Scope declared: `target_uris`, `allowed_origins`/`allowed_paths`, `forbidden_zones`
- [ ] Action ceiling declared: `max_actions`, `max_duration_seconds`
- [ ] Approval-required action classes listed explicitly in manifest
- [ ] Credential access set to `false` (or Tier 1 grant documented if true)
- [ ] External network flag set correctly
- [ ] Vault protected files are in `forbidden_zones`
- [ ] Workflow manifest registered in `runtime/workflows/registry/`
- [ ] Role card assigned with appropriate permission ceiling
- [ ] Test run executed in shadow mode (no real actions) before first live execution
- [ ] Failure and recovery behavior confirmed

---

*Graph links: [[OpenClaw-Runtime-Profile]] · [[06_AGENTS/Vault-Map|Vault-Map]] · [[Full-System-Operator-Surface]] · [[Browser-Operator-Surface]] · [[Operator-Surface-Adapter-Spec]] · [[06_AGENTS/Autonomous-Operator-Runtime|Autonomous-Operator-Runtime]] · [[Credential-Boundaries-SOP]] · [[Untrusted-Input-Handling-SOP]] · [[06_AGENTS/Agent-Security-Model|Agent-Security-Model]] · [[06_AGENTS/Permission-Matrix|Permission-Matrix]]*

*Full-System-Operator-Safety-SOP.md — v1.0 | Created: 2026-04-15 | Phase 9 sub-track | Safety protocol for all FSOS executions | Covers scope, approval escalation, credentials, high-risk actions, stop conditions, failure escalation, audit, and operator intervention*
