# runtime/ — ChaseOS Runtime Layer

> Machine-readable policy files, runtime state, workflow substrate, coordination surfaces, and enforcement footholds.
> This folder is the implementation-facing runtime substrate for ChaseOS.

---

## What This Folder Contains

```text
runtime/
├── README.md
├── chaseos_gate.py
├── policy/
├── aor/
├── workflows/
├── acquisition/
├── memory/
├── bindings/
├── state/
├── agent_bus/
└── ...
```

---

## Core Subsystems

### Policy Layer
`runtime/policy/`
- protected files
- task profiles
- adapter manifests
- gateway allowlists
- machine-readable enforcement declarations

`runtime/policy/gateway_allowlists.json` is the machine-readable boundary for gateway-controlled writes, task types, external APIs, control-plane transports, and credential-reference forms. Setup, config, schedule, and gateway-facing mutation paths must route through the Gate checks before writing runtime state.

### AOR Workflow Layer
`runtime/aor/` and `runtime/workflows/registry/`
- autonomous operator runtime substrate
- workflow registry
- task routing
- bounded writeback and audit flow

### Runtime Navigation Memory Layer
`runtime/memory/nav/`
- per-runtime navigation overlays
- trusted zones, safe write paths, escalation points
- machine-readable runtime self-orientation

### Runtime Bootstrap and User Attachment Layer
`runtime/bindings/`
- bootstrap contract schema
- detachable user-attachment contract schema
- example bootstrap and attachment records

### Runtime State Resolver Layer
`runtime/state/`
- canonical runtime-state schema
- resolver foothold
- generated current runtime-state artifact
- generated fail-closed error artifact
- local CLI-shaped runtime inspection foothold

### Runtime Lifecycle Layer
`runtime/lifecycle/`
- machine-readable runtime lifecycle records
- start / stop / restart / health contract shapes
- seeded OpenClaw and Hermes lifecycle records

### Runtime Coordination Bus Layer
`runtime/agent-bus-example/`
- durable runtime-to-runtime coordination substrate
- task packet schema
- event schema
- heartbeat schema
- durable state model
- control-surface ingress translation target for coordination-sensitive work
- communication-infrastructure-agnostic coordination layer

### Provider Registry and State Ledger Layer
`runtime/providers/`
- provider catalog and setup-status inspection
- runtime provider/fallback governance status aggregation
- Runtime Provider Governance Layer (RPGL): provider strength/task-class capability gate, shared execution-adapter fallback capability gate for Hermes/OpenClaw synthesis, runtime adapter-governance RPGL consumption report, fallback timeout decisions, simulated timeout proof, local Ollama streaming timeout wrapper contract with injected stream runner, queue-on-denial records, primary cooldown/recovery/unhealthy state, active provider target profile reporting, target-profile plan/proposal/queue requests, read-only provider config reconciliation, provider config correction proposal/queue requests, provider config apply preflight, non-executing provider config apply design, provider config apply approval request artifacts, immutable provider config apply approval decision records, provider config apply immutable decision-record validation/idempotency preflight, provider config apply decision consumption plan, provider config apply decision consumer design, provider config apply decision consumer invocation preflight, provider config apply decision consumer implementation plan, provider config apply decision consumer writer dry-run, provider config apply decision consumer write-guard contract, guarded provider config apply decision consumer record writer, guarded provider config apply atomic marker writer, provider config apply atomic marker writer design, guarded provider config apply live executor with rollback-on-verification-failure, provider config apply executor dry-run plan, denied-by-default live probe Gate approval schema, pending approval request artifacts, target-profile-aware live-probe approval plan/request writer, immutable live-probe approval decision records, live-probe approval decision CLI preview/write, structural approval validation, non-executing live-probe decision preflight, non-executing live-probe marker contract, guarded live-probe decision consumer record writer, guarded live-probe atomic marker writer, non-network live-probe executor dry-run/readiness reports, read-only live-smoke readiness report, read-only live-smoke closeout plan, read-only completion-status report, guarded live-probe executor result records/provider-state update, non-executing live-probe executor spec/precondition reports, and provider audit events
- append-only provider-state event ledger for rate limits, cooldowns, fallback activation, and recovery-to-primary evidence
- machine-readable provider call-surface audit separating runtime model execution from Source Intelligence, connector/acquisition, delivery, lifecycle, setup, and dry-run adapter telemetry

### SiteWorkflow / Website Workflow Index Layer
`runtime/SiteWorkflow/`
- dry-run-only ChaseOS SiteWorkflow production scaffold
- global catalog templates, local tenant installation fixture, schemas, and compatibility JSON registry
- SiteSkillTemplate, WorkflowTemplate, tenant installs, credential refs, browser profile refs, provider bindings, budget policies, approval requests, scoped run records, and append-only audit events
- validation and dry-run planning for governed website/provider workflows with tenant/workspace/user scope
- scoped artifacts under `docs/framework-logs/SiteWorkflow-Runs/`, `docs/framework-logs/SiteWorkflow-Audits/`, and `docs/framework-logs/SiteWorkflow-Approvals/`
- no live browser execution, provider API calls, authenticated session handling, posting, purchasing, billing/account mutation, broker/trading action, or canonical promotion authority in this slice

---

## Gateway Allowlist and Credential Boundary

The Gate now denies gateway-sensitive actions unless they match explicit allowlists in `runtime/policy/gateway_allowlists.json`.

Allowlisted boundary classes:
- write targets
- task types
- external APIs
- control-plane transports
- credential references

Setup and gateway commands must not write secret values into ChaseOS state. They may write only environment variable names, keychain or local-secret references, or template placeholders. The setup-state writer enforces that boundary before updating `runtime/setup_state.json`.

---

## Runtime State Resolver Layer

`runtime/state/` is the first implementation foothold for ChaseOS runtime self-knowledge.

Seeded artifacts:
- `runtime/state/README.md`
- `runtime/state/runtime-state.schema.json`
- `runtime/state/current_state.example.json`
- `runtime/state/resolver.py`
- `runtime/state/runtime_cli.py`
- `runtime/state/CLI-README.md`
- `runtime/state/COMMAND-CONTRACT-README.md`

Generated artifacts expected later:
- `runtime/state/current_state.json`
- `runtime/state/last_error.json`

This layer is subordinate to:
- `06_AGENTS/ChaseOS-Runtime-State-and-Gateway-Design.md`
- `06_AGENTS/Portable-Runtime-Identity-and-User-Binding.md`
- adapter manifests in `runtime/policy/adapters/`
- runtime bootstrap and attachment contracts in `runtime/bindings/`

Its purpose is to give ChaseOS one canonical machine-readable answer to current runtime posture before future CLI, localhost interface, or gateway surfaces are added.

It now also provides the first local runtime inspection command foothold, and the richer promoted top-level/runtime command tree has now been aligned with an installable `chaseos` entrypoint path.

That means this runtime layer now underpins:
- `chaseos runtime inventory`
- `chaseos runtime status`
- `chaseos runtime provider-status`
- `chaseos runtime adapter-governance`
- `chaseos runtime providers`
- `chaseos runtime fallback-status`
- `chaseos runtime queue list/show`
- `chaseos runtime provider probe primary|fallback`
- `chaseos runtime provider probe primary|fallback --probe-mode network-dry-run`
- `chaseos runtime provider probe primary|fallback --probe-mode live-preflight`
- `chaseos runtime provider probe primary|fallback --probe-mode live-preflight --write-approval-request`
- `chaseos runtime provider probe primary|fallback --probe-mode live-preflight --gate-approval-id <id>`
- `chaseos runtime provider executor-spec primary|fallback`
- `chaseos runtime provider executor-spec primary|fallback --gate-approval-id <id>`
- `chaseos runtime browser-cdp approval-request [target_url] --write-approval-request`
- `chaseos runtime browser-cdp approval-request --gate-approval-id <id>`
- `chaseos runtime browser-cdp executor-spec [target_url]`
- `chaseos runtime browser-cdp executor-spec [target_url] --gate-approval-id <id>`
- `chaseos runtime browser-cdp decision-preflight [target_url] --gate-approval-id <id>`
- `chaseos runtime browser-cdp idempotency-reservation-spec [target_url] --gate-approval-id <id>`
- `chaseos runtime browser-cdp executor-dry-run [target_url] --gate-approval-id <id>`
- `chaseos runtime browser-cdp approval-decision-policy [target_url] --gate-approval-id <id>`
- `chaseos runtime browser-cdp approval-decision-consumer-design [target_url] --gate-approval-id <id>`
- `chaseos runtime browser-cdp atomic-marker-writer-design [target_url] --gate-approval-id <id>`
- `chaseos runtime browser-cdp isolated-browser-launcher-design [target_url] --gate-approval-id <id>`
- `chaseos runtime browser-cdp isolated-launcher-implementation-preflight [target_url] --gate-approval-id <id>`
- `chaseos runtime browser-cdp closeout-readiness [target_url] --gate-approval-id <id>`
- `chaseos runtime browser-cdp approval-decision --gate-approval-id <id> --write-approval-decision --decision approved`
- `chaseos runtime browser-cdp execute [target_url] --gate-approval-id <id>`
- `chaseos runtime provider config-report`
- `chaseos runtime provider target-profile`
- `chaseos runtime provider target-profile-plan [MODEL]`
- `chaseos runtime provider target-profile-plan gpt-5.5 --write-approval-request --requested-by operator`
- `chaseos runtime provider config-plan`
- `chaseos runtime provider config-plan --write-approval-request --requested-by operator`
- `chaseos runtime provider config-apply-preflight <proposal_id>`
- `chaseos runtime provider config-apply-design <proposal_id>`
- `chaseos runtime provider config-apply-approval-request <proposal_id> --write-approval-request --requested-by operator`
- `chaseos runtime provider config-apply-approval-request <proposal_id> --gate-approval-id <id>`
- `chaseos runtime provider config-apply-approval-decision <proposal_id> --gate-approval-id <id> --decision approved|denied`
- `chaseos runtime provider config-apply-decision-preflight <proposal_id> --gate-approval-id <id>`
- `chaseos runtime provider config-apply-decision-consumption-plan <proposal_id> --gate-approval-id <id>`
- `chaseos runtime provider config-apply-decision-consumer-design <proposal_id> --gate-approval-id <id>`
- `chaseos runtime provider config-apply-decision-consumer-preflight <proposal_id> --gate-approval-id <id>`
- `chaseos runtime provider config-apply-decision-consumer-implementation-plan <proposal_id> --gate-approval-id <id>`
- `chaseos runtime provider config-apply-decision-consumer-writer-dry-run <proposal_id> --gate-approval-id <id>`
- `chaseos runtime provider config-apply-decision-consumer-write-guard-contract <proposal_id> --gate-approval-id <id>`
- `chaseos runtime provider config-apply-decision-consumer <proposal_id> --gate-approval-id <id> --write-consumer-record`
- `chaseos runtime provider config-apply-atomic-marker-writer <proposal_id> --gate-approval-id <id> --write-consumption-marker`
- `chaseos runtime provider config-apply-atomic-marker-writer-design <proposal_id> --gate-approval-id <id>`
- `chaseos runtime provider config-apply-executor <proposal_id> --gate-approval-id <id> --apply-provider-config`
- `chaseos runtime provider config-apply-executor-dry-run <proposal_id> --gate-approval-id <id>`
- `chaseos runtime provider live-probe-approval-decision primary|fallback --gate-approval-id <id> --decision approved|denied`
- `chaseos runtime provider live-probe-decision-preflight primary|fallback --gate-approval-id <id>`
- `chaseos runtime provider live-probe-marker-contract primary|fallback --gate-approval-id <id>`
- `chaseos runtime provider live-probe-decision-consumer primary|fallback --gate-approval-id <id> --write-consumer-record`
- `chaseos runtime provider live-probe-atomic-marker-writer primary|fallback --gate-approval-id <id> --write-consumption-marker`
- `chaseos runtime provider live-probe-executor-dry-run primary|fallback --gate-approval-id <id>`
- `chaseos runtime provider live-probe-target-approval-plan [primary|fallback|all]`
- `chaseos runtime provider live-probe-target-approval-plan primary --write-approval-request --requested-by operator`
- `chaseos runtime provider live-smoke-readiness`
- `chaseos runtime provider live-smoke-closeout-plan`
- `chaseos runtime provider completion-status`
- `chaseos runtime provider live-probe-executor primary|fallback --gate-approval-id <id> --execute-live-probe`
- `chaseos runtime provider fallback-timeout-proof no-chunks`
- `chaseos runtime provider ollama-timeout-contract success`
- `chaseos gate check-operation runtime.provider.config_apply`
- `chaseos gate check-operation runtime.provider.live_probe --external-api provider.openai`
- `chaseos runtime recover --dry-run`
- `chaseos runtime audit-tail`
- `chaseos runtime health`
- `chaseos runtime health-debug`
- `chaseos osril sessions/show/events/approvals/wait-resume/respond/resume-ready`
- `chaseos config list/validate/set`
- `chaseos gate ...`
- `chaseos agent-bus status`
- `chaseos agent-bus route`
- `chaseos agent-bus heartbeat`
- `chaseos agent-bus task create|claim|update`

The runtime substrate is no longer only proving a future CLI shape. It is now feeding the actual promoted command tree that is being prepared for installed use.

The 2026-05-02 RPGL shared adapter fallback gate pass tightened `runtime/execution_adapters/execute.py`: after a primary provider failure, high-authority tasks are queued for primary retry instead of falling through to fallback, weak fallback is denied for non-weak-safe task classes, and weak fallback is still allowed for weak-safe recovery tasks after simulated rate-limit. Non-rate primary failures can mark the primary provider `unhealthy` before route/queue decisions. Focused verification is green in `runtime/tests/test_runtime_provider_governance_layer.py`.

The 2026-05-02 RPGL live-probe decision-record pass added immutable live-probe approval decision records under `docs/framework-logs/Agent-Activity/_rpgl_provider_live_probe_decisions/` and made provider `executor-spec` report whether a matching approved immutable decision exists. It remains non-executing: no live provider call, secret read, provider-state mutation, queue drain, gateway restart, idempotency marker write, approval consumption, or CLI execution path is added in this pass.

The 2026-05-02 RPGL live-probe decision CLI and marker-contract pass added guarded `chaseos runtime provider live-probe-approval-decision`, `live-probe-decision-preflight`, and `live-probe-marker-contract` surfaces. These can preview/write immutable live-probe decision records, validate approval/decision/marker preconditions, and preview the future marker path under `runtime/providers/state/provider_live_probe_markers/`, while still performing no provider call, secret read, provider-state mutation, queue drain, gateway restart, approval/decision consumption, or marker write.

The 2026-05-02 RPGL live-probe consumer/marker writer pass added guarded `chaseos runtime provider live-probe-decision-consumer ... --write-consumer-record` and `chaseos runtime provider live-probe-atomic-marker-writer ... --write-consumption-marker`. These write only the separate live-probe consumer record and create-new marker after a valid approved immutable decision exists; live probe execution, provider calls, secret reads, provider-state mutation, queue drain, and gateway restart remain disabled.

The 2026-05-03 RPGL live-probe executor implementation pass added guarded `chaseos runtime provider live-probe-executor ... --execute-live-probe`. It requires the complete approval/decision/consumer/marker chain, writes a create-new result record under `runtime/providers/state/provider_live_probe_results/`, updates provider state from the bounded probe outcome, blocks duplicate execution, and keeps queue drain, gateway restart, provider config edits, canonical docs mutation, and fallback stickiness disabled. Targeted tests verify this with an injected runner; real OpenAI/Ollama live-provider smoke remains unverified.

The 2026-05-03 RPGL local fallback timeout proof pass added `chaseos runtime provider fallback-timeout-proof first-token|no-chunks|wall-time|post-chunk-no-chunks`. This is a deterministic simulated-stream proof harness: it does not call providers, read secret values, sleep for timeout duration, drain queues, restart gateways, or mutate canonical docs. It feeds simulated observations through RPGL timeout evaluation/recording, including no-chunk unhealthy marking, so real local fallback timeout state and audit behavior are testable before a live Ollama wrapper is approved and verified.

The 2026-05-03 RPGL local Ollama wrapper timeout contract pass added `chaseos runtime provider ollama-timeout-contract success|first-token|no-chunks|wall-time|post-chunk-no-chunks`. This exercises the concrete local Ollama fallback wrapper with injected stream observations, builds a bounded Ollama request payload with `stream: true` and `num_ctx: 16384`, denies high-authority work before stream execution, enforces first-token/no-chunk/wall-time aborts, marks no-chunk fallback unhealthy, and proves no provider call, secret read, queue drain, gateway restart, canonical mutation, or fallback stickiness occurs. Live Ollama endpoint smoke remains unverified.

The 2026-05-03 RPGL adapter consumption closeout pass added `chaseos runtime adapter-governance` and extended `runtime/adapters/runtime_governance.py` with `rpgl_consumption` checks. The report proves the shared execution adapter imports/uses RPGL route, rate-limit, unhealthy-state, capability-gate, and high-authority queue markers; Hermes review synthesis consumes the shared adapter as `execution_adapter="hermes"`; and OpenClaw watch remains bus-dispatch-only without direct provider call markers. This is read-only/static governance evidence, not live provider smoke.

The 2026-05-03 RPGL live-smoke readiness closeout pass added `chaseos runtime provider live-smoke-readiness`. The command is read-only and reports final live-smoke blockers across model config truth, local fallback setup, live-probe approval requests, decision/consumer/marker/result chain readiness, and no-execution flags. Current repo truth is blocked: Hermes/OpenClaw configs still declare Claude models rather than the active RPGL provider target profile, local Ollama fallback is not configured, existing live-probe approval requests are pending Anthropic/Claude records, and the live-probe decision/consumer/marker/result chain is incomplete.

The 2026-05-03 RPGL live-smoke closeout plan pass added `chaseos runtime provider live-smoke-closeout-plan`. The command is read-only and returns the ordered governance sequence required before final live smoke: config truth verification, provider config approval/apply chain, local Ollama fallback metadata decision with `num_ctx: 16384`, target-matching live-probe approvals, immutable live-probe decisions, consumer/marker records, and the final guarded executor command. It performs no provider call, secret read, provider-state mutation, queue drain, gateway restart, approval write, config apply, marker write, or canonical mutation.

The 2026-05-03 RPGL provider target profile pass added `chaseos runtime provider target-profile` and made config reconciliation target-profile aware. GPT-5.5 remains the current legacy compatibility default when no profile file exists, but it is no longer treated as permanent source-code truth: future operator profiles can declare per-runtime primary models, per-runtime fallback targets, fallback enforcement, provider setup defaults, and local fallback metadata without code changes.

The 2026-05-03 RPGL target-profile plan pass added `chaseos runtime provider target-profile-plan [MODEL]`. It builds a portable candidate profile for the current or supplied model, preserves runtime fallback chains, keeps fallback enforcement observe-only by default, and can write only a proposal artifact under `docs/framework-logs/Agent-Activity/_rpgl_provider_target_profile_proposals/` plus a `needs_operator_approval` queue item. It does not write `runtime/providers/provider_target_profile.json`, edit model config, mutate provider/setup state, call providers, read secrets, drain queues, or restart gateways.

The 2026-05-03 RPGL target live-probe approval pass added `chaseos runtime provider live-probe-target-approval-plan [primary|fallback|all]`. The command builds pending live-probe approval templates from the active target profile, so the approval path follows the current/future selected model instead of a hardcoded GPT-5.5 assumption. With `--write-approval-request`, it writes only pending approval request artifacts for ready candidates. Disabled/unconfigured local fallback is reported as blocked, and no provider call, secret read, provider-state mutation, target-profile file write, config apply, queue drain, gateway restart, decision/marker write, or live smoke execution occurs.

The 2026-05-03 RPGL final completion audit pass added `chaseos runtime provider completion-status`. The command reports the RPGL feature as complete for governed implementation with live OpenAI/Ollama proof deferred pending operator-approved approval/decision/consumer/marker evidence. It reports zero remaining major development passes, the current target profile/model posture, local fallback metadata including `num_ctx: 16384`, queue/provider summaries, acceptance-criteria status, and live-smoke blockers while rejecting write/apply/execute flags and performing no provider call, secret read, provider-state mutation, queue drain, gateway restart, approval write, config apply, marker write, or canonical mutation.

The 2026-05-03 RPGL recovery/queue retry proof pass strengthened `chaseos runtime queue retry <id> --dry-run` and `chaseos runtime recover --dry-run`. Queue retry now returns a dry-run retry package preserving the original request, task class, required provider strength, required context files, primary/fallback failure reasons, approval state, retry state, and primary eligibility while reporting that no queue drain, retry-attempt increment, provider call, secret read, provider-state mutation, canonical file mutation, fallback use, or gateway restart occurred. Recovery dry-run now includes the same retry package previews for open queue items.

The 2026-05-03 RPGL live-probe executor dry-run/readiness pass added `chaseos runtime provider live-probe-executor-dry-run ... --gate-approval-id <id>`. It composes provider metadata, Gate posture, approval, immutable decision, consumer record, marker, setup metadata, secret-reference metadata, and timeout policy into one non-network readiness report. It still does not call providers, read secrets, mutate provider state, drain queues, restart gateways, or enable live probe execution.

The 2026-04-27 provider-status pass added a read-only control-plane snapshot for:
- active runtime selection posture
- operator default provider state
- runtime primary/fallback model declarations
- provider setup validity
- agent-bus queued, active, stuck, and no-chunk task posture
- Hermes/OpenClaw heartbeat and optional lifecycle-probe health

The follow-on provider-state-ledger pass added `runtime/providers/state/provider_state_events.jsonl` as the canonical append-only event ledger for provider attempts, rate-limit hits, cooldown windows, fallback activation, and recovery-to-primary evidence. `runtime provider-status` now reads this ledger and reports `no_events`, `active`, `observed`, `inactive`, `fallback_active`, `primary_recovery_eligible`, or `primary_recovered` from recorded events. The shared execution adapter now emits request, rate-limit, fallback-activation, and primary-recovery-completed events into that ledger, and Hermes review synthesis routes through that shared adapter instead of a direct Anthropic helper. The call-surface audit at `runtime/providers/provider_call_surfaces.json` now classifies adjacent provider-like calls so Source Intelligence generation/embedding, capture/acquisition connectors, Discord/Whop delivery, setup/lifecycle probes, and OpenAI/n8n dry-run surfaces do not get mistaken for runtime fallback-governance emitters. Perplexity, Grok, RSS, web scrape, IMAP email, Google Docs, and Google Drive live-source outcomes now write acquisition connector-health telemetry to `runtime/acquisition/state/connector_health_events.jsonl`, inspectable through `chaseos acquisition connector-health`, without feeding those outcomes into provider fallback state. Discord webhook and Whop forum-post delivery outcomes now write SBP delivery-health telemetry to `runtime/sbp/state/delivery_health_events.jsonl`, inspectable through `chaseos sbp delivery-health`, without feeding those outcomes into provider fallback state. `runtime provider-status` now also includes an adjacent `adapter_health_rollup` over those connector-health and delivery-health ledgers while preserving the provider-state boundary, plus a presentation-only `operator_summary` with status cards, attention items, and recommended next actions for Studio/operator wrapping. The 2026-04-29 governed recovery-to-primary pass added the d... [truncated]

The 2026-04-30 RPGL probe continuation added metadata-only `--probe-mode network-dry-run` provider probe plans and documented Discord read-only/dry-run command mappings in the RPGL spec. The follow-on live-probe preflight pass added `--probe-mode live-preflight` as a denied-by-default contract surface that reports required operator/Gate approval metadata without calling providers, reading secrets, or mutating provider state. The Gate approval-schema pass declared `runtime.provider.live_probe` with schema `rpgl.live_provider_probe.v1`; `chaseos gate check-operation runtime.provider.live_probe --external-api provider.openai --json` returns the non-executing approval template. The provider config reconciliation pass added `chaseos runtime provider config-report`; the 2026-05-03 target-profile pass now makes that report compare current runtime/setup/model metadata against the active RPGL provider target profile plus local fallback context posture without mutating provider config, reading secrets, or calling providers. The provider config plan and apply chain (`config-plan`, `config-apply-preflight`, `config-apply-design`, approval requests, immutable decisions, consumers, markers, and guarded executor) remain Gate-governed and auditable.

The 2026-04-30 Browser CDP continuation added `chaseos runtime browser-cdp executor-spec` as a non-executing precondition report for a future local CDP read-only proof. It reuses the declared `browser.cdp.read_only_proof` Gate schema and reports approval persistence/validation state, missing approval consumption, browser launcher, CDP client, and idempotency prerequisites while keeping browser launch, CDP connection, screenshot capture, DOM snapshot, credential/cookie/session access, trusted skill writes, and canonical writeback disabled. The follow-on approval-artifact pass added `chaseos runtime browser-cdp approval-request`, which writes pending request-only records under `docs/framework-logs/Agent-Activity/_bosl_cdp_approvals/` and can structurally validate them without consuming approval or authorizing execution. The 2026-05-01 decision-preflight pass added `chaseos runtime browser-cdp decision-preflight`, which reads a supplied approval artifact, checks approval status, checks future idempotency-marker posture, and previews a bounded future write plan while still performing no approval consumption, marker write, browser launch, CDP connection, screenshot/DOM capture, trusted write, Agent Bus enqueue, provider call, or canonical writeback. The follow-on idempotency-reservation pass added `chaseos runtime browser-cdp idempotency-reservation-spec`, which computes the future marker path, marker record template, atomic create-new rules, and blocked status without writing the marker or enabling execution. The executor dry-run pass added `chaseos runtime browser-cdp executor-dry-run`, which computes future execution order, stop conditions, artifact plan, and feature tracker without consuming approval, writing markers, launching a browser, connecting to CDP, or writing evidence. The approval-decision policy pass added `chaseos runtime browser-cdp approval-decision-policy`, which computes future decision record and consumption rules without writing a decision, consuming approval, ... [truncated]

 The approval-decision consumer design pass added `chaseos runtime browser-cdp approval-decision-consumer-design`, which computes the future single-use consumer algorithm, immutable consumption template, marker-absence guard, and forbidden field policy without writing a decision, consuming approval, writing a marker, launching a browser, connecting to CDP, or writing proof artifacts. The isolated-browser-launcher design pass added `chaseos runtime browser-cdp isolated-browser-launcher-design`, which defines the local-only throwaway-profile launch contract while proving no browser process, profile, port, CDP connection, marker, or proof artifact is created by the design surface. The follow-on implementation preflight added `chaseos runtime browser-cdp isolated-launcher-implementation-preflight`, which checks opaque launcher metadata, loopback port allocation policy, no-shell process runner policy, cleanup policy, and bounded CDP client binding. Hermes later activated the bounded approved read-only proof path with a local throwaway-profile smoke; `closeout-readiness` reports that activation when the build-log evidence is present.

The Browser CDP operational activation pass verified the implemented live browser runtime on this WSL host: user-local Chromium is discoverable, the bounded executor consumed one approved decision, wrote an exact-once marker, launched an isolated profile, connected to local CDP, and produced screenshot/DOM proof artifacts against a throwaway localhost target.

The 2026-04-28 config-validation pass added read-only `chaseos config validate` over `config/chaseos-example/config.yaml`. It reports ready/blocked posture, allowed schema keys, invalid value/path issues, and secret-like key placement without mutating config or expanding Gate authority. This narrows final config/settings polish toward future Studio rendering rather than basic Runtime Shell validation.

The 2026-04-25 dogfood pass also demonstrated a live coordination ingress path through the promoted shell:
- lifecycle health probe resolution for OpenClaw and Hermes
- top-level bus routing inspection
- fresh bus heartbeats
- top-level task create, claim, and update flow

The 2026-04-26 follow-on hardening pass extended that proof into the focused AOR review-coordination lane:
- bounded fallback parsing now covers the reviewed manifest/role-card/task-type/model-config surfaces needed for the review path when `PyYAML` is unavailable
- `runtime/aor/engine.py` now resolves workflow handlers lazily, reducing unrelated import-time dependency poisoning
- focused verification is green across:
  - `runtime/agent-bus-example/test_agent_bus.py`
  - `runtime/agent-bus-example/test_capabilities_router.py`
  - `runtime/agent-bus-example/test_bus_coordination_e2e.py`

The same hardening pass then cleared the next broader YAML-resilience gaps:
- `runtime/schedules/loader.py` now fails closed without import-time `PyYAML` dependency, with bounded fallback parsing for live schedule intent and workflow-registry shapes
- `runtime/workflows/graph_hygiene.py` now uses bounded frontmatter parsing instead of a hard `yaml` import for proposal-only hygiene scans
- `runtime/graph/extractor.py` now uses bounded manifest/frontmatter parsing instead of a hard `yaml` import for graph extraction
- focused verification is green for:
  - `runtime/schedules/test_schedule_bridge.py`
  - `runtime/graph/test_graph_substrate.py`

The next highest-impact runtime substrate gap is now the remaining maintenance-workflow and test-only surfaces that still hard-import `yaml`.

---

## Sync Requirements

| Policy file | Must match | When to update |
|-------------|-----------|----------------|
| `policy/gateway_allowlists.json` | `06_AGENTS/ChaseOS-Gate.md` + `runtime/SETUP-README.md` + `runtime/COMMANDS.md` | When gateway write targets, task types, external APIs, transports, or credential-reference rules change |
| `policy/adapters/openclaw.yaml` | `06_AGENTS/OpenClaw-Adapter-Spec.md` + `OPENCLAW.md` | When OpenClaw runtime scope, write posture, or approval rules change |
| `memory/nav/*/nav-map.json` | `06_AGENTS/Runtime-Navigation-Map.md` + runtime profile docs | When runtime routes, trust zones, or escalation boundaries change |
| `bindings/runtime-bootstrap.schema.json` | `06_AGENTS/Portable-Runtime-Identity-and-User-Binding.md` | When bootstrap contract structure changes |
| `bindings/user-attachment.schema.json` | `06_AGENTS/Portable-Runtime-Identity-and-User-Binding.md` + `CORE_MANIFEST.md` | When detachable user-binding structure changes |
| `state/runtime-state.schema.json` | `06_AGENTS/ChaseOS-Runtime-State-and-Gateway-Design.md` | When canonical runtime-state structure changes |
| `lifecycle/runtime-lifecycle.schema.json` | `06_AGENTS/ChaseOS-Runtime-Lifecycle-Contract.md` | When runtime lifecycle record structure changes |
| `agent_bus/*.schema.json` + `agent_bus/sqlite_schema.sql` | `06_AGENTS/Runtime-InterAgent-Coordination-Bus.md` | When coordination packet/state rules change |
| `providers/state_ledger.py` + `providers/governance_layer.py` + `providers/state/provider_state_events.jsonl` + `providers/state/provider_state.json` + `providers/state/provider_queue.json` + `providers/state/provider_audit.jsonl` + `providers/state/provider_config_apply_markers/` + `providers/state/provider_config_apply_results/` + `docs/framework-logs/Agent-Activity/_rpgl_provider_approvals/` + `docs/framework-logs/Agent-Activity/_rpgl_provider_config_apply_approvals/` + `providers/adapter_health.py` + `providers/provider_call_surfaces.json` + `providers/RECOVERY-TO-PRIMARY.md` + `execution_adapters/execute.py` + `sbp/delivery_health.py` | `runtime/COMMANDS.md` + `06_AGENTS/ChaseOS-CLI-Command-Reference.md` + `06_AGENTS/Runtime-Provider-Governance-Layer.md` | When provider-state event schema, RPGL routing/queue/audit state, live-probe approval artifacts, provider config apply approval artifacts, provider config apply idempotency marker/result contract, adapter-health rollup, recovery-to-primary contract, call-surface classification, adapter emission, delivery-health output, provider-status output, or runtime provider-governance CLI output changes |
| `osril/approvals.py` + `osril/wait_resume.py` + `osril/resume_ready.py` | `runtime/osril/README.md` + `runtime/COMMANDS.md` + `06_AGENTS/ChaseOS-CLI-Command-Reference.md` | When OSRIL approval, wait/resume, resume-ready, or command-surface behavior changes |
| `SiteWorkflow/*.py` + `SiteWorkflow/catalog/**` + `SiteWorkflow/tenants/**` + `SiteWorkflow/registry/**` + `SiteWorkflow/schemas/**` | `06_AGENTS/ChaseOS-SiteWorkflow.md` + `06_AGENTS/SiteWorkflow-Multi-User-Production-Architecture.md` + `runtime/COMMANDS.md` + `06_AGENTS/ChaseOS-CLI-Command-Reference.md` | When SiteWorkflow production objects, tenant scope, dry-run behavior, command surface, audit/approval shape, or execution boundary changes |

---

## What Belongs Here vs Vault

| Type | Lives in | Why |
|------|----------|-----|
| YAML/JSON policy, state, and manifests | `runtime/` | Machine-readable runtime substrate |
| Python runtime modules | `runtime/` | Implementation and enforcement footholds |
| Human-readable architecture docs | `06_AGENTS/` | Reviewable doctrine and design |
| Logs and audits | `docs/framework-logs/` | Historical trace |

---

*Graph links: [[06_AGENTS/ChaseOS-Gate|ChaseOS-Gate]] · [[06_AGENTS/Autonomous-Operator-Runtime|Autonomous-Operator-Runtime]] · [[Portable-Runtime-Identity-and-User-Binding]] · [[ChaseOS-Runtime-State-and-Gateway-Design]] · [[06_AGENTS/Runtime-InterAgent-Coordination-Bus|Runtime-InterAgent-Coordination-Bus]] · [[Control-Plane-Ingress-and-Bus-Translation]] · [[ChaseOS-Commands-and-CLI-Surfaces]]*


*Graph links auto-wired by vault_hygiene (2026-04-24): [[CLI-README]] . [[COMMAND-CONTRACT-README]] . [[COMMANDS-README]] . [[DEPENDENCIES]] . [[IMPLEMENTATION-NOTES]] . [[MANUAL-VALIDATION]] . [[OPERATOR-README]] . [[STATUS-NOTES]]*


*Graph links auto-wired by vault_hygiene (2026-04-24): [[COMMANDS]]*


*Graph links auto-wired by vault_hygiene (2026-04-25): [[HEALTH-README]] . [[LIFECYCLE-README]] . [[TOP-LEVEL-COMMAND-NOTES]] . [[provenance_schema]]*


*Graph links auto-wired by vault_hygiene (2026-04-25): [[ROOT-CAUSE-NOTES]]*


*Graph links auto-wired by vault_hygiene (2026-04-26): [[CLI-PROMOTION-NOTES]] . [[PROBE-CONTRACT-NOTES]] . [[SETUP-README]]*


*Graph links auto-wired by vault_hygiene (2026-04-26): [[Runtime-Registry-Folder-Guide]]*


*Graph links auto-wired by vault_hygiene (2026-05-01): [[20260427-095156-example-project-rss-coindesk]] . [[20260427-095156-example-project-rss-decrypt]] . [[20260430__candidate-run-123]] . [[CODEX_CLI_LIVE_TEST_HANDOFF]] . [[DELIVERY-HEALTH]] . [[codex-live-subprocess-policy-block]] . [[codex-shell-policy-block]] . [[codex-stdout]] . [[grok_digest]] . [[grok_digest.template]] . [[perplexity_digest]] . [[perplexity_digest.template]] . [[research_export]] . [[youtube_summary]] . [[youtube_summary.template]]*


*Graph links auto-wired by vault_hygiene (2026-05-02): [[20260501-095138-example-project-rss-coindesk]] . [[20260501-095138-example-project-rss-decrypt]]*


*Graph links auto-wired by vault_hygiene (2026-05-04): [[2026-05-04-SiteWorkflow-shadow-replay-candidate-run-123]] . [[shadow-replay-candidate-run-123]]*
