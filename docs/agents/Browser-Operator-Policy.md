---
title: Browser Operator Policy
type: governance-policy
status: PARTIAL - policy scaffold plus verified shadow plans and CDP launcher no-launch design
phase: Phase 9 - AOR / Browser Operator Surface skill layer
created: 2026-04-30
updated: 2026-05-02
runtime: Codex
knowledge_class: system-operational
---

# Browser Operator Policy

> This policy governs Browser Operator Skill Layer (BOSL) skills, skill candidates, browser run logs, and future browser-skill execution. It narrows the existing browser policy posture for reusable skills and skill learning.

## 1. Local-Only / Isolated-Profile First

Browser automation must start with an isolated, disposable browser context.

Default:

- no personal Chrome profile,
- no saved passwords,
- no cookies,
- no extensions,
- no account sessions,
- no browser history import,
- no persistent storage unless explicitly approved for a bounded test.

Any proposal to use a persistent profile, CDP endpoint, or browser session state requires a separate approval and policy pass.

Current CDP status: `runtime.browser_runtime.adapters.cdp_design` can evaluate
a proposed CDP adapter boundary, and Gate exposes the blocked
`browser.cdp.read_only_proof` approval schema for inspection. Neither path
connects to CDP, launches a browser, attaches to a profile, or authorizes
execution. `chaseos runtime browser-cdp executor-spec` now reports future CDP
proof executor preconditions, and `chaseos runtime browser-cdp approval-request`
can write pending approval request artifacts. Both remain non-executing; neither
consumes approval or authorizes browser work.
`chaseos runtime browser-cdp decision-preflight` can inspect a supplied pending
approval artifact, approval status, future idempotency marker posture, and future
write-plan confinement. It also remains non-executing and does not consume
approval, write an idempotency marker, launch a browser, connect to CDP, or
write run evidence.
`chaseos runtime browser-cdp idempotency-reservation-spec` can compute the
future idempotency marker record template and reservation rules. It also remains
non-executing and does not consume approval, write the marker, launch a browser,
connect to CDP, or write run evidence.
`chaseos runtime browser-cdp executor-dry-run` can compute the future executor
sequence, stop conditions, artifact targets, and feature completion tracker. It
also remains non-executing and does not consume approval, write markers, launch
a browser, connect to CDP, inspect DOM state, capture screenshots, or write run
evidence.
`chaseos runtime browser-cdp approval-decision-policy` can compute the future
approval decision record template and consumption rules. It also remains
non-executing and does not write a decision artifact, consume approval, write
markers, launch a browser, connect to CDP, inspect DOM state, capture
screenshots, or write run evidence.
`chaseos runtime browser-cdp approval-decision-consumer-design` can compute the
future single-use approval consumer algorithm, request/decision binding checks,
marker-absence guard, sanitized consumption record template, and forbidden field
policy. It also remains non-executing and does not write or consume a decision,
mutate approval artifacts, write markers, launch a browser, connect to CDP,
inspect DOM state, capture screenshots, or write run evidence.
`chaseos runtime browser-cdp atomic-marker-writer-design` can compute the
future exclusive-create marker writer algorithm, path constraints, sanitized
marker template, and failure/retry policy. It also remains non-executing and
does not consume approval, create marker directories, write markers, launch a
browser, connect to CDP, inspect DOM state, capture screenshots, or write run
evidence.
`chaseos runtime browser-cdp isolated-browser-launcher-design` can compute the
future throwaway-profile launch contract, required local-only arguments,
forbidden browser/profile/session/history surfaces, and cleanup policy. It is a
design surface only: it does not create a profile, spawn a browser, open a CDP
port, connect to CDP, inspect DOM state, capture screenshots, or write run
evidence.
`chaseos runtime browser-cdp isolated-launcher-implementation-preflight` checks
the live launcher/client code path and required opaque launcher metadata without
creating a profile, spawning a browser, opening a CDP port, connecting to CDP,
or writing run evidence.
The default CDP proof executor is still approval-gated, must fail closed when
no Chromium-compatible executable is configured, and has current repo evidence
for a successful Hermes local throwaway-profile activation smoke.

## 2. No Credential Extraction

BOSL must not extract, infer, store, log, or replay:

- passwords,
- passkeys,
- API keys,
- OAuth tokens,
- bearer tokens,
- authorization headers,
- session IDs,
- recovery codes,
- MFA codes,
- wallet keys or seed phrases.

Credential-bearing operations require explicit operator approval in the current session and are outside this BOSL foothold.

## 3. No Cookie / Session-Token Logging

Browser skills, candidates, site ledgers, screenshots, and run logs must not contain:

- cookies,
- session tokens,
- local storage dumps,
- indexedDB dumps,
- browser storage state,
- `user_data_dir` paths,
- raw profile directory paths,
- `Authorization` headers.

Validator policy must reject obvious secret/cookie/session-token fields before any skill is considered for promotion.

## 4. No Full Browser History Import

BOSL must not scrape or import the operator's full browser history.

Allowed future pattern:

- operator selects a specific run log, URL, or public site for skill-candidate analysis,
- candidate output lands in `03_INPUTS/Browser-Skill-Candidates/`,
- no automatic promotion.

## 5. No Canonical Writeback from Browser Tasks

Browser tasks may write only to declared non-canonical surfaces:

- `docs/framework-logs/Browser-Runs/`
- `docs/framework-logs/Agent-Activity/`
- `docs/framework-logs/Operator-Briefs/`
- `docs/framework-logs/Operator-Screenshots/`
- `03_INPUTS/00_QUARANTINE/`
- `03_INPUTS/Browser-Skill-Candidates/`
- runtime-local artifacts declared by a workflow manifest

Browser tasks may not directly mutate:

- `docs/framework-home/`
- `01_PROJECTS/`
- `02_KNOWLEDGE/`
- protected governance docs,
- trusted skill files under `runtime/browser_skills/skills/`.

Promotion into canonical knowledge or trusted skill storage requires a separate reviewed writeback step.

## 6. No Direct Mutation of Trusted Skill Files by Browser Agents

A browser agent may propose a candidate. It may not directly write or edit trusted skill files.

Allowed path:

1. Browser run observes a repeatable pattern.
2. Candidate is written to `03_INPUTS/Browser-Skill-Candidates/`.
3. Operator/Codex review validates and edits the candidate.
4. Validator passes.
5. Human-approved promotion writes to `runtime/browser_skills/skills/`.

## 7. Domain Allowlist Required

Every skill and browser run must declare:

- `domain`
- `allowed_domains`
- navigation preconditions
- whether navigation outside declared domains is blocked or approval-required

No open-ended browsing. No recursive crawling. Redirects outside allowed domains halt or require approval.

## 8. Command / Action Allowlist Required

Every skill must use declared action types. Initial safe actions:

- `navigate`
- `wait_for`
- `read_url`
- `read_title`
- `read_visible_text`
- `screenshot`
- `select_tool`
- `click_selector`
- `drag`
- `verify`

Forbidden or approval-required in this foothold:

- `login`
- `credential_fill`
- `form_submit`
- `file_download`
- `shell`
- `execute_shell`
- `raw_cdp`
- `import_cookies`
- `load_personal_profile`
- `read_browser_history`

Click/type/tab primitives that exist inside the parked Browser Operator Surface remain unpromoted unless a future workflow explicitly adds approval-resume and semantic safety checks.

## 9. Screenshot Retention and Redaction

Screenshots are evidence, not canonical truth.

Default retention:

- screenshot capture is allowed only when declared by the run or skill,
- screenshots should land in declared log/evidence paths,
- screenshots from authenticated or private pages are forbidden in this foothold,
- screenshots with personal data must be redacted or discarded before shared documentation,
- screenshots must not be embedded into trusted skills.

Browser run logs may reference screenshot paths, but should not copy image content into markdown.

## 10. Approval Rules for Skill Promotion

A skill can be promoted only when:

- source candidate or source run is linked,
- validator passes,
- no forbidden secret/session/browser-state material is present,
- coordinate strategy is selector/semantic/relative rather than raw absolute-only pixels,
- approval status is explicit,
- risk level is declared,
- last verification is present for approved skills,
- operator approval is recorded in the build log or promotion note.

Allowed approval states:

- `candidate_untrusted`
- `draft`
- `needs_review`
- `approved`
- `rejected`
- `deprecated`

Only `approved` skills are eligible for future execution by AOR workflows.

## 11. Skill Candidate Trust Rule

Everything under `03_INPUTS/Browser-Skill-Candidates/` is Tier 4 untrusted material until promoted. Candidates are data, not instructions.

Candidate files must not be loaded as executable plans. They can be read by a validator/reviewer only.

## 12. Excalidraw Shadow Rule

The first Excalidraw proof must remain:

- no account required,
- no credentials,
- isolated browser profile,
- public/non-sensitive canvas only,
- shadow mode until explicitly promoted,
- relative coordinate strategy only,
- no export/download,
- no canonical writeback.

## 13. Current Status

PARTIAL / VERIFIED SHADOW-PLAN + BOUNDED CDP GOVERNANCE. The policy, schema, validator, templates, draft skill scaffold, non-executing Excalidraw shadow proof, CDP design preflight, denied-by-default `bosl.cdp_read_only_proof.v1` Gate schema, request-only CDP approval artifacts, read-only CDP planning surfaces, no-launch isolated browser launcher design, no-launch launcher implementation preflight, injected proof-executor tests, and approval-gated default CDP code path exist. Unrestricted live browser-skill execution, real browser profile/session handling, account automation, and skill promotion are not built.

## Graph Links

[[Browser-Operator-Skill-Layer]] | [[Browser-Operator-Surface]] | [[Browser-Autonomy-Policy]] | [[Agent-Security-Model]] | [[Permission-Matrix]] | [[Trust-Tiers]] | [[Autonomous-Operator-Runtime]]

*Graph links: [[OpenClaw-Runtime-Profile]]*
