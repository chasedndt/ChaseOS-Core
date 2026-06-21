# runtime/agent_bus/ — Runtime Agent Bus README

> ChaseOS-owned coordination substrate for runtime-to-runtime task routing, ownership, blockers, review, results, and heartbeats.
> This folder is intentionally communication-infrastructure-agnostic: Discord, CLI, future Studio panels, and other control surfaces are ingress/visibility layers. The bus is the coordination layer.

---

## Purpose

Use `runtime/agent_bus/` when ChaseOS needs structured coordination between runtimes.

Examples:
- Hermes decomposes operator intent and hands executable work to OpenClaw
- OpenClaw returns a result for Hermes review
- either runtime raises a blocker
- runtimes emit heartbeats/liveness state
- operator-facing control panels need to inspect real coordination state rather than chat summaries

Do **not** use this layer as:
- a canonical truth store
- a permission grant
- a protected-file bypass
- a replacement for AOR, role cards, workflow manifests, or Gate
- a chat transcript archive

---

## Core Rule

**Control panels are ingress. The bus is coordination.**

That means:
- Discord channels, Discord threads, CLI surfaces, and future standalone/operator panels may receive operator input
- if that input becomes coordination-sensitive runtime work, it should be translated into structured bus state
- the bus, not the transport, should hold task ownership and runtime coordination state
- human-facing summaries are mirrors of bus state, not substitutes for it

See canonical doctrine:
- `06_AGENTS/Runtime-InterAgent-Coordination-Bus.md`
- `06_AGENTS/Control-Plane-Ingress-and-Bus-Translation.md`
- `06_AGENTS/Coordination-Bus-Summary-Context-Application.md`

---

## What Lives Here

- `task-packet.schema.json` — schema for bounded task handoff packets; runtime identity fields are generic strings, while live runtime validity is enforced by the bus capability registry / API
- `event.schema.json` — schema for immutable coordination events; event sender identity is likewise runtime-generic
- `heartbeat.schema.json` — schema for runtime liveness records
- `sqlite_schema.sql` — durable local data model for the bus
- `bus.py` — helper module for status, claim/update, heartbeat, stale expiry, and watch-loop behavior
- `examples/` — reference packet shapes
- `agent_bus.sqlite` — local durable coordination store

---

## Primary Coordination Objects

### Task packet
A bounded unit of work moving between runtimes.

Typical fields:
- `task_id`
- `from`
- `to`
- `intent`
- `status`
- `request`
- `expected_output`

### Event record
Immutable state transition or note for a task.

Examples:
- created
- claimed
- in progress
- blocked
- review requested
- done
- expired

### Heartbeat
Compact per-runtime or per-runtime-instance liveness and posture record.

Examples:
- runtime name
- runtime instance id
- control-surface scope
- current task
- health state
- last seen

Current lifecycle truth:
- heartbeats refresh through explicit `upsert_heartbeat(...)` calls or CLI/watch helpers that invoke it
- the bus does not autonomously invent fresh liveness; a runtime or wrapper must publish the heartbeat
- the direction for ChaseOS now explicitly includes instance-aware heartbeats so one live Hermes/OpenClaw process can be distinguished from another across Discord channels, Discord threads, CLI sessions, and future ingress surfaces

---

## Task lifecycle behavior (current truth)

### Heartbeat refresh
Heartbeat state is refreshable now, but by explicit write paths rather than ambient self-refresh.

Current live refresh surfaces include:
- `chaseos agent-bus heartbeat ...`
- `watch --runtime <runtime-or-capability-alias> ...` which calls `upsert_heartbeat(...)`; seeded examples include `Hermes`, `OpenClaw`, `Codex`, and capability-declared aliases such as `Axiom-Codex`; explicit watch identity fields such as `--runtime-instance-id`, `--control-surface`, and `--control-surface-key` are preserved for both one-shot and interval watch loops
- any workflow/runtime wrapper that explicitly invokes `upsert_heartbeat(...)`

### Stale-task expiry
The bus can expire stale owned work now.

Current rule:
- only tasks in active owned states are considered (`claimed`, `in_progress`, `blocked`, `review`)
- unowned `open` tasks are not auto-expired
- the owning runtime must actually be stale by heartbeat/liveness rules
- task age must exceed the supplied expiry threshold
- expiry records an immutable `expired` event

### Completed-task retention
Completed (`done`) or cancelled tasks are currently retained in SQLite as coordination history.

Important current limitation:
- the bus does **not** yet implement a separate archive/export/compaction lane for completed tasks
- there is no automatic removal of done tasks from the primary store
- current history posture is retention-in-place plus immutable event trail, not archive rotation

This means the current bus behaves more like:
- a durable coordination state/history store
than:
- a self-pruning queue with archival rollover

---

## Runtime Identity

Seeded live runtime identifiers include:
- `Hermes`
- `OpenClaw`
- `Codex`

The bus packet/schema footing is runtime-generic. Promoted inspection surfaces such as `task list --recipient/--owner` accept arbitrary runtime identifiers so future registered runtime instances can be inspected without another schema or parser rewrite. Side-effecting runtime operations are still bounded by adapter/Gate policy and live capability registration.

`Codex` is a bounded development worker registered through `runtime/codex/capabilities.yaml` and `runtime/policy/adapters/codex.yaml`. It handles code/repo/test task packets through `runtime/adapters/codex/` and returns reviewable artifacts; it is not a canonical truth store or broad runtime owner.

These names are for machine coordination, not chat flair.

---

## Allowed Intents

Use the narrow coordination intent set:
- `TASK`
- `RESULT`
- `BLOCKER`
- `REVIEW`
- `QUESTION`
- `HEARTBEAT`
- `NOTICE`

If a message cannot be expressed in one of these forms, it probably should not become a coordination packet yet.

---

## Supported Control-Surface Pattern

Recommended ingress flow:
1. operator input arrives from Discord, CLI, future Studio, or another bounded control surface
2. ChaseOS classifies whether it is advisory, direct workflow execution, approval, or coordination-sensitive work
3. if coordination-sensitive, it is translated into bus state
4. runtimes act from the bus state within their existing authority bounds
5. human-facing surfaces receive summaries and links back to the real state/artifacts

This is what keeps ChaseOS transport-neutral and scalable to future control surfaces.

---

## Current CLI Surface

Current coordination helpers include:
- `chaseos agent-bus status`
- `chaseos agent-bus runtimes`
  - now exposes reduced runtime liveness plus raw heartbeat-instance rows for each runtime
  - this is the operator-facing view for seeing when one runtime is active across multiple Discord/control lanes
- `chaseos agent-bus route <task-type>`
- `chaseos agent-bus task list`
  - accepts runtime-generic `--recipient/--to` and `--owner` filters for inspection, plus `--limit N` to keep large backlog previews bounded
- `chaseos agent-bus task create`
  - supports ingress metadata such as platform/channel/thread/conversation/message IDs
  - can now derive `conversation_key` automatically for Discord ingress from channel/thread identity when the caller only supplies source IDs
  - can now derive a stable default `work_fingerprint` from Discord `origin_message_id` when the caller does not provide one explicitly
  - now carries optional `control_plane_route` metadata so the machine-readable ingress route can stay visible alongside the task packet
  - supports `--work-fingerprint` for mirrored-work dedupe
  - supports optional execution constraints through `--no-shell-commands`, `--no-live-subprocess`, `--write-policy`, and `--allowed-write-path`; these are stored on the task as `execution_constraints` metadata for recipient adapters to narrow execution, not to grant new authority
- `chaseos agent-bus task claim`
- `chaseos agent-bus task update`
- `chaseos agent-bus task cleanup`
  - supports preview-by-default queue hygiene over matched task slices using filters such as recipient, sender, status, request text, updated-before timestamps, and lane identifiers (`work_fingerprint`, `conversation_key`, `origin_message_id`)
  - reports total matches separately from the selected limited slice (`matched_count` / `matched_task_ids` versus `selected_count` / `selected_tasks` / `selected_task_ids`), so `--limit` keeps preview payloads bounded without hiding backlog size
  - only mutates the selected matched tasks when `--apply --status open` is supplied, so operators can inspect noisy backlog slices before cancelling them and cannot accidentally cancel mixed or active states
- `chaseos agent-bus task reclaim`
- `chaseos agent-bus heartbeat`
  - now supports explicit instance-aware publication fields such as `--runtime-instance-id`, `--heartbeat-scope`, `--control-surface`, and `--control-surface-key`
  - these fields are now forwarded correctly through the promoted top-level `python chaseos.py agent-bus heartbeat ...` surface as well, not just the inner agent-bus CLI
  - the canonical parser accepts capability-declared runtime aliases for actor/runtime fields, normalizes aliases for Gate policy lookup, and lets the bus store canonical runtime identity while preserving alias-derived instance identity
- `chaseos agent-bus ingress discord`
  - translates a bounded Discord/control-plane request into bus-owned task state when the bound channel posture allows it
  - leaves runtime-chat traffic advisory by default unless the request is explicitly classified coordination-sensitive
- `chaseos agent-bus expire-stale`
- `chaseos agent-bus watch --runtime <runtime-or-capability-alias> --once|--interval N`
  - watch now accepts `--runtime-instance-id`, `--control-surface`, and `--control-surface-key` so operator-owned one-shot and long-running loops can publish liveness under the same instance/control-surface identity as explicit heartbeat writes

Canonical command hardening:
- side-effecting smoke coverage now exercises the top-level `python chaseos.py agent-bus ...` surface, not the subsystem `runtime/agent_bus/cli.py` parser
- smoke tasks run against a disposable `--vault-root` test vault, not the live vault
- smoke-created tasks are cancelled with a unique cleanup marker, and the live bus database is checked to confirm that marker did not land in real task or heartbeat state

Validated promoted shell flow now includes:
1. runtime health inspection through `python chaseos.py runtime health --runtime all --json`
2. heartbeat refresh through `python chaseos.py agent-bus heartbeat ...`
3. routing inspection through `python chaseos.py agent-bus route operator-briefing`
4. runtime capability/liveness inspection through `python chaseos.py agent-bus runtimes`
5. task creation through `python chaseos.py agent-bus task create ...`
6. claim/update progression through `python chaseos.py agent-bus task claim ...` and `python chaseos.py agent-bus task update ...`
7. bounded queue hygiene preview/apply through `python chaseos.py agent-bus task cleanup ... [--apply]`
8. reclaim-path validation through `python chaseos.py agent-bus task reclaim ...` with active-state guardrails
9. end-to-end AOR review-path validation through `python -m pytest runtime/agent_bus/test_bus_coordination_e2e.py -q`
10. canonical no-pollution smoke validation through `python -m pytest runtime/agent_bus/test_canonical_agent_bus_cli.py -q -p no:cacheprovider`

Current caveat:
- `watch --stale-after-seconds 0` used to be too blunt and could mass-expire active tasks; the hardening pass now narrows expiry to active owned tasks whose owner runtime is actually stale
- stale-runtime detection is now decoupled from task-age expiry thresholds, so `max_age_seconds` no longer doubles as a heartbeat-staleness override
- focused bus tests now cover the hardened behavior: stale owned active work expires, open unowned work does not, and active work with a fresh owner heartbeat does not
- the fallback capability parser now fails closed on malformed manifests, so capability routing degrades more safely when `PyYAML` is unavailable
- the focused AOR review coordination lane now passes without `PyYAML` by combining bounded fallback parsing in registry/role-card/task-type loaders with lazy workflow-handler resolution in `runtime/aor/engine.py`
- promoted `agent-bus watch` now supports both one-shot refresh (`--once`) and a ChaseOS-owned long-running refresh loop (`--interval N`) without requiring a separate transport-owned watcher implementation
- task ownership now has an `owner_instance` lane alongside runtime `owner`, so Hermes/OpenClaw Discord-thread and shared Discord-channel claims can align with instance-scoped heartbeats; remaining instance work is routing/liveness expansion across non-Discord control surfaces
- ingress-aware task storage is now live enough to carry Discord platform/channel/thread/conversation/message metadata plus `work_fingerprint`, and active same-recipient duplicate fingerprints now fail closed instead of creating parallel mirrored work items
- task and event JSON schemas are now aligned with the generic runtime substrate: they no longer hard-code `Hermes` / `OpenClaw` enums even though those remain the seeded runtime examples
- Discord-origin create-time normalization is now live too: malformed Discord ingress without `source_channel_id` is rejected fail-closed, `conversation_key` / `control_plane_route` can be auto-filled from lane identity, and `origin_message_id` can seed the default fingerprint when callers have not computed one yet
- `watch_once --claim-next` now also suppresses open tasks that conflict with already-active conversation/message scope for the same runtime, reducing same-lane double-claim behavior even when mirrored work slips past create-time dedupe
- the promoted `agent-bus task cleanup ...` path now exists as a bounded queue-hygiene surface, letting operators preview or cancel matched noisy backlog slices without mutating the entire bus blindly
- when a claimed task carries Discord ingress context, `watch_once` now writes an instance-scoped heartbeat keyed to that lane so liveness can be observed at a more real control-surface granularity than runtime-only heartbeats
- the promoted `runtimes` / `status` CLI surfaces now expose those heartbeat-instance rows alongside the reduced runtime summary, so this identity model is visible to operators instead of only existing in storage internals
- the promoted `agent-bus ingress discord ...` path now resolves the live channel binding map and can translate one coordination-sensitive Discord/control-plane request into one bus-owned task, but real bot/gateway wiring into that seam is still an honest remaining closure item
- broader lifecycle verbs such as `start`, `stop`, `restart`, and `logs` remain target-shape commands until their runtime-operation policy allowlist and approval rules are explicit

These are ingress/inspection helpers around the bus substrate.
They do not replace the bus as the underlying coordination state.

---

## Runtime Bridge Docs

Runtime-local behavior is further described in:
- `runtime/openclaw/coordination_bridge.md`
- `runtime/hermes/coordination_bridge.md`

These tell each runtime how to participate without expanding authority.

---

## Coordination vs Execution

The bus is not the same thing as execution.

- bus = routing, ownership, blockers, results, liveness
- AOR = bounded workflow execution
- Gate = enforcement and writeback control
- control panels = ingress and visibility

This distinction is the reason ChaseOS can support multiple runtimes and multiple control surfaces without collapsing into chat-based machine state.

---

## Standalone Direction

For how this layer should map into future standalone/operator surfaces, see:
- `06_AGENTS/Runtime-Agent-Bus-and-Coordination-Standalone-Application.md`

For summary behavior on top of this layer, see:
- `06_AGENTS/Coordination-Bus-Summary-Context-Application.md`

---

*Authority docs: `06_AGENTS/Runtime-InterAgent-Coordination-Bus.md` · `06_AGENTS/Control-Plane-Ingress-and-Bus-Translation.md`*

*Graph links: [[06_AGENTS/Runtime-InterAgent-Coordination-Bus|Runtime-InterAgent-Coordination-Bus]] · [[Control-Plane-Ingress-and-Bus-Translation]] · [[Runtime-Shell-and-Command-Surface-Summary-Context-Application]] · [[ChaseOS-Commands-and-CLI-Surfaces]]*
