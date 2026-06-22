# ChaseOS Sub-Agent Presets

Status: PARTIAL / PASS 9 AGENT BUS TASK PACKET PREVIEW IMPLEMENTED

This folder contains editable, task-scoped sub-agent instruction presets for ChaseOS.
Presets are not daemons and do not start runtime processes. They are lightweight
role files that can be loaded, validated, routed to an available runtime, and
converted into a bounded activation context.

Current repo truth:
- `HermesAgent` maps to the existing Agent Bus runtime `Hermes`.
- `OpenClaw` maps to the existing Agent Bus runtime `OpenClaw`.
- `OpenHuman` is modeled by the router but blocked by default because current
  ChaseOS docs mark OpenHuman as retired/reference-only.

Pass 1 surfaces:
- Preset markdown files under `subagents/presets/`
- Team templates under `subagents/teams/`
- Schemas under `subagents/schemas/`
- Runtime loader/router/policy helpers under `runtime/subagents/`

Pass 2 surfaces:
- `chaseos subagents list`
- `chaseos subagents show PRESET_ID`
- `chaseos subagents validate`
- `chaseos subagents route-preview PRESET_ID`

Pass 3 surfaces:
- `chaseos subagents approval-preview PRESET_ID`

Pass 4 surfaces:
- `chaseos subagents write-approval-request PRESET_ID`

Pass 5 surfaces:
- `chaseos subagents approval-consumption-dry-run --approval-artifact-path PATH`

Pass 6 surfaces:
- `chaseos subagents approval-review-decision --approval-artifact-path PATH --decision approve|deny`

Pass 7 surfaces:
- `chaseos subagents approval-consumption-decision-binding --approval-artifact-path PATH --decision-artifact-path PATH`

Pass 8 surfaces:
- `chaseos subagents approval-consumption-exact-once-marker-contract --approval-artifact-path PATH --decision-artifact-path PATH`

Pass 9 surfaces:
- `chaseos subagents agent-bus-task-packet-preview --approval-artifact-path PATH --decision-artifact-path PATH`

Boundaries:
- No always-on sub-agent daemon is created.
- No Agent Bus task is enqueued by this layer.
- The approval-request writer may write one pending approval artifact only when
  explicitly called with `--write-approval-request` and an exact
  `--expected-work-fingerprint`.
- No approval is granted or consumed by the approval-preview or
  approval-request layers.
- The approval-consumption dry-run validates a pending request artifact,
  expected work fingerprint, recomputed route, and future exact-once marker path,
  and remains non-consuming.
- The approval-review-decision contract may write one immutable approve/deny
  decision artifact only when explicitly called with
  `--write-approval-decision` and an exact `--expected-work-fingerprint`.
- A written approval decision does not mutate the pending request, consume
  approval, write a marker, enqueue Agent Bus work, start a daemon, dispatch a
  runtime, or call providers/browsers. Full approval consumption remains unbuilt.
- The approval-consumption decision binding preflight validates one pending
  request plus one recorded approved decision and can report readiness for a
  future executor, but it still does not consume approval, consume the decision,
  write an exact-once marker, enqueue Agent Bus work, start a daemon, dispatch a
  runtime, or call providers/browsers.
- The approval-consumption exact-once marker contract validates the same
  request/decision binding and may write one create-only marker under
  `07_LOGS/Agent-Activity/_subagent_activation_approvals/_consumption_markers/`
  only when explicitly called with `--write-consumption-marker` and an exact
  `--expected-work-fingerprint`. The marker reserves future consumption but
  does not mutate request or decision artifacts, consume the decision, enqueue
  Agent Bus work, start a daemon, dispatch a runtime, or call providers/browsers.
- The Agent Bus task packet preview reads the pending request, approved decision,
  and recorded marker, then builds an inert task packet shape with strict
  execution constraints. It does not write an Agent Bus task, enqueue work, start
  a daemon, dispatch a runtime, call providers/browsers, mutate governed memory,
  or write canonical state.
- No provider, browser, Discord, schedule, payment, CRM, or website action is executed.
- Memory writes are reviewable proposals only unless an existing ChaseOS approval path consumes them later.
