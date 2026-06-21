# runtime/memory/

Layered runtime memory substrate for ChaseOS.

## Layer C

Runtime-specific memory lives here:

- `adapters/<runtime_id>/profile.json` - behavioral runtime profile
- `adapters/<runtime_id>/identity-ledger.json` - Agent Identity Ledger behavioral record
- `nav/<runtime_id>/nav-map.json` - runtime navigation overlay
- `scorecards/<runtime_id>.json` - execution outcome history
- `repair/<runtime_id>.json` - execution repair memory

Layer C is advisory. It can guide routing, preflight, and inspection, but it
cannot override Gate policy, role cards, schedules, workflow manifests, or
current source truth.

## Layer D

Task-local memory lives in:

```text
runtime/tasks/
```

Layer D context is active-task/workspace scoped. It does not become durable
runtime memory unless explicitly promoted through a governed pass.

## CLI

```powershell
chaseos memory status
chaseos memory summary --json
chaseos memory list
chaseos memory show openclaw
chaseos memory show claude
chaseos memory tasks
chaseos memory validate
```

`memory summary` is the consolidated read-only posture surface for Phase 10
memory-inspector wrapping. It combines JSON validation, Layer C runtime-family
coverage, Layer D task-context counts, advisory governance flags, attention
items, and next actions. It does not mutate memory, promote task memory, apply
repair memory, or grant runtime authority.

## Pulse / Context Memory Core Scaffold

The first ChaseOS Pulse pass adds schema primitives for Context Memory Core:

- `context_events.py` - event records for observations, corrections, decisions,
  feedback, project state, schedule context, and runtime reflection.
- `memory_atoms.py` - candidate memory units for Layers B/C/D/E.
- `memory_clusters.py` - candidate groupings of related memory atoms.
- `temporal_facts.py` - candidate/current/expired facts with validity windows.
- `personal_map.py` - Personal Map / user profile graph node and edge schema.
- `feedback_rules.py` - feedback evaluation rules and durable rule candidates
  that create candidates only.
- `candidate_store.py` - append-only pending-review Personal Map candidate logs
  under `07_LOGS/Pulse-Decks/memory-candidates/personal-map/`.

These modules are schema-first and non-mutating. They do not write to
`02_KNOWLEDGE/`, do not mutate `Now.md`, do not mutate Project-OS files, and do
not grant runtime authority.

Feedback rules are candidate rules for future ranking, suppression, memory
candidate creation, personal-map linking, project linking, or agent-brain
linking. They do not apply card feedback, approve memory, create tasks, or
promote knowledge without a later governed review/writeback pass.

The candidate store is a Pulse log artifact queue, not a second datastore. Its
read-only queue builder does not mutate the Personal Map, approve memory, create
tasks, change Project-OS files, or enable canonical writeback.
