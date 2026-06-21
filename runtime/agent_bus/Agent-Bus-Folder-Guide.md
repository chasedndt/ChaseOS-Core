# runtime/agent_bus/ — N-Runtime Coordination Substrate

> Durable machine coordination layer for Hermes ↔ OpenClaw work inside ChaseOS.
> Designed from day one for more than two runtimes. This folder is the runtime-local coordination substrate. It does not grant permissions by itself.

---

## Purpose

This layer exists so runtimes can coordinate through structured state instead of noisy free-chat.

Use it for:
- task handoff
- result return
- blocker escalation
- review requests
- heartbeats
- translation target for coordination-sensitive operator input coming from Discord, CLI, or future control surfaces

Do **not** use it as:
- canonical truth store
- approval authority
- protected-file bypass
- replacement for AOR workflow governance

See also:
- `06_AGENTS/Runtime-InterAgent-Coordination-Bus.md` (authority doc)
- `06_AGENTS/Control-Plane-Ingress-and-Bus-Translation.md`

---

## Recommended Runtime Pattern

- SQLite is the primary durable machine-state store.
- JSON packet schemas define interoperable payloads.
- Discord mirrors summaries for humans.

---

## Contents

### Schemas and store
- `task-packet.schema.json` — task handoff packet schema
- `event.schema.json` — immutable task event schema
- `heartbeat.schema.json` — runtime liveness schema
- `sqlite_schema.sql` — local durable schema for the coordination bus
- `examples/` — reference packet shapes
- `agent_bus.sqlite` — local durable coordination store (bootstrap-created)

### Routing layer
- `capabilities.py` — filesystem-based capability registry; discovers `runtime/*/capabilities.yaml`; provides `get_eligible_runtimes()`, `load_all_capabilities()`, `discover_runtime_names()`
- `router.py` — liveness-aware + capacity-aware task type router; provides `route_task_type()`, `get_runtime_liveness()`, `get_stale_runtimes()`
- `bus.py` — core task lifecycle: `create_task()`, `claim_task()`, `reclaim_task()`, `update_task_status()`, `upsert_heartbeat()`, `mark_stale_tasks()`, `watch_once()`; watch cycles can carry optional runtime instance/control-surface identity for daemon loops that need instance-scoped heartbeat and task ownership while preserving the bus runtime label

### CLI (via `chaseos agent-bus`)
- `status` / `task list|claim|update|cleanup` / `heartbeat` / `expire-stale` / `watch --once|--interval N`
- `task claim TASK_ID --runtime RUNTIME [--runtime-instance-id INSTANCE_ID]` — claim work for a runtime and optionally pin the owner to a specific control-surface lane
- `route --task-type TYPE` — show recommended runtime for a task type
- `runtimes` — list all registered runtimes with their capabilities
- `task create` — create a validated task packet on the bus
- `task create --no-shell-commands --no-live-subprocess --write-policy none` — attach task-level `execution_constraints` metadata so recipient adapters can narrow shell, live-subprocess, and write behavior without relying only on daemon flags
- `task cleanup` — preview or cancel selected noisy backlog slices through bounded filters, including Discord/lane identifiers (`work_fingerprint`, `conversation_key`, `origin_message_id`), instead of hand-editing SQLite state; total matches remain visible through counts/IDs while `--limit` bounds returned selected task payloads, and `--apply` requires explicit `--status open`
- `task reclaim TASK_ID` — re-open a task from a stale runtime to a new recipient
- `mode` — show current backend mode (local or server) without opening `bus_config.yaml`
- `heartbeats [--runtime NAME]` — list all heartbeat records; optionally filter by runtime

---

### Canonical command smoke rules
- command hardening tests invoke the canonical top-level shell (`python chaseos.py agent-bus ...`), not subsystem scripts
- side-effecting smoke tests must pass `--vault-root` to a disposable test vault or explicitly cancel any created task with a known cleanup message
- current canonical smoke coverage creates, heartbeats, watches/claims, reclaims, translates Discord ingress, cancels created work, and asserts the live bus database is not polluted by the smoke marker

---

## Routing Layer

The routing layer is the dispatch-time intelligence above the raw bus.

### Capability manifests

Each runtime declares capabilities in `runtime/{runtime}/capabilities.yaml`:

```yaml
runtime: openclaw
bus_name: OpenClaw
handles:
  - task_type: operator-briefing
    priority: primary
  - task_type: review
    priority: secondary
max_concurrent_tasks: 3
heartbeat_stale_seconds: 900
priority_ceiling: normal
```

Adding a new runtime = adding `runtime/{new_runtime}/capabilities.yaml`. The router picks it up automatically. No code changes. The SQLite substrate and packet/event JSON schemas no longer hard-code runtime-name CHECK/enum constraints, so future runtimes do not require a schema rewrite just to participate.

### Routing logic

`route_task_type(task_type, vault_root)` applies three filters in order:

1. **Eligibility** — which runtimes declare they handle this task type (from capabilities.yaml)
2. **Liveness** — filter to runtimes whose heartbeat is within `heartbeat_stale_seconds`
3. **Concurrent load** — filter to runtimes where active task count < `max_concurrent_tasks`

Recommended runtime = first runtime that passes all three filters (sorted by declared priority: primary before secondary before tertiary).

If all live runtimes are at capacity, the router still recommends the first live one — the task may queue. If all eligible runtimes are stale, `recommended` is `None`.

### Enforcement

`create_task()` enforces two constraints before writing to the bus:

| Constraint | Rule |
|---|---|
| **Known runtime** | sender and recipient must be in the capability registry (or hard-coded fallback set) |
| **Priority ceiling** | task priority must not exceed `recipient.priority_ceiling` (low=0, normal=1, high=2, critical=3) |

Priority ceiling is fail-open: if no capabilities.yaml is found for the recipient, the ceiling check is skipped and SQL is the final enforcement layer.

### Execution constraints metadata

Tasks may carry optional `execution_constraints` metadata:

| Field | Meaning |
|---|---|
| `allow_shell_commands` | Whether the recipient adapter may use shell-capable execution for this task. |
| `allow_live_subprocess` | Whether the recipient adapter may start a live subprocess at all. |
| `write_policy` | One of `adapter-default`, `declared-paths`, or `none`. |
| `allowed_write_paths` | Optional explicit write-path list for adapters that support declared write scopes. |

This metadata narrows recipient behavior. It does not grant new permissions,
override adapter manifests, bypass Gate policy, or authorize canonical-state
writes. Codex consumes these fields when building strict Codex task packets.

### Current runtime declarations

| Runtime | `bus_name` | `priority_ceiling` | `max_concurrent_tasks` |
|---|---|---|---|
| openclaw | OpenClaw | normal | 3 |
| hermes | Hermes | high | 2 |
| codex | Codex | normal | 1 |

---

## Canonical Runtime Names

Use only `bus_name` values in packets (not filesystem runtime folder names):
- `Hermes`
- `OpenClaw`
- `Codex`

Adding a new runtime: create `runtime/{new_runtime}/capabilities.yaml`; runtime validity is enforced by the bus capability registry / API rather than by Hermes/OpenClaw-only packet-schema enums.

---

## Allowed Intents

- `TASK`
- `RESULT`
- `BLOCKER`
- `REVIEW`
- `QUESTION`
- `HEARTBEAT`
- `NOTICE`

---

## State Rules

Allowed task states:
- `open`
- `claimed`
- `in_progress`
- `blocked`
- `review`
- `done`
- `cancelled`
- `expired`

One owner per task, with optional instance ownership.
`owner` is the runtime bus name (`Hermes`, `OpenClaw`).
`owner_instance` is the lane identity when the task is tied to a control surface such as a Discord thread or shared runtime channel.
When omitted, Discord-origin claims derive `owner_instance` as `discord-thread-{source_thread_id}`, `discord-channel-{source_channel_id}`, or `discord-lane-{conversation_key}` fallback.
No ambient chat as machine state.
Every meaningful transition should create an event record.
Stale tasks (owned by a runtime with an old heartbeat) can be reclaimed with `reclaim_task()`.

---

## Notes Annotation Convention

The bus `intent` field (`TASK`, `REVIEW`, etc.) identifies the communication category but is too coarse to route within a multi-workflow runtime. A runtime like OpenClaw handles `operator-briefing`, `graph-hygiene`, `vault-maintenance`, and `scheduled-briefing` — all arriving as `intent: TASK`.

### Sender convention

When creating a task destined for a multi-workflow runtime, embed routing hints in the `notes` field using `key: value` lines:

```
task_type: operator-briefing
workflow: operator_close_day
```

**`task_type`** — the logical task category the recipient should handle. Maps directly to the recipient's internal dispatch table. This is the primary routing key.

**`workflow`** — optional refinement within a task type. Use when a task type supports multiple concrete workflows (e.g., `operator-briefing` covers both `operator_today` and `operator_close_day`). If absent, the recipient uses its default for that task type.

**Precedence:** `task_type:` annotation > `workflow:` annotation > `intent` field > recipient default.

### Recipient convention

Watch loops parse `notes` on each claimed task before dispatching:

1. Extract `task_type:` annotation → primary dispatch key
2. If absent, extract `workflow:` annotation → map to task_type via workflow registry
3. If absent, fall back to intent-based inference (`REVIEW` → `review`)
4. If still unresolved → escalate as `blocked` (do not silently drop)

### Example packet notes

```
task_type: operator-briefing
workflow: operator_close_day
requested_by: Hermes
priority_reason: end-of-day schedule window
```

### When you do NOT need annotations

- Single-workflow runtimes (Hermes currently handles only `review`) — intent alone is sufficient.
- `REVIEW` intent tasks — unambiguous; no annotation needed.
- Tasks where the recipient's default is correct — omit `workflow:` and let the runtime choose.

### Adding a new dispatch target

1. Add the task type to the recipient's `capabilities.yaml` `handles:` list.
2. Add a handler function to the recipient's watch loop `_TASK_DISPATCH` dict.
3. Document the expected `task_type:` value in the recipient's `coordination_bridge.md`.
4. No bus schema changes required.

---

## Bridge Docs

- `runtime/openclaw/coordination_bridge.md`
- `runtime/hermes/coordination_bridge.md`

These tell each runtime where to read and how to behave. A new runtime needs its own bridge doc.

---

*Authority doc: `06_AGENTS/Runtime-InterAgent-Coordination-Bus.md`*

*Graph links: [[OpenClaw-Runtime-Profile]] · [[Hermes-Runtime-Profile]]*
