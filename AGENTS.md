# Codex Operating Notes for ChaseOS

When working in this repo, Codex should join the ChaseOS agent bus as a worker,
not as a core runtime owner.

## Bus identity

- Bus name: `Codex`
- Capability manifest: `runtime/codex/capabilities.yaml`
- Adapter boundary: `runtime/adapters/codex/`

## Allowed role

Codex may help with:

- `code.review`
- `code.patch`
- `repo.inspect`
- `test.run`

Codex should return structured proposals, patches, risks, blockers, or completion
evidence. ChaseOS/OpenClaw remains the arbiter for applying patches and writing
memory/core/runtime governance state.

## Live bus daemon

Codex can be run as a live bus worker with:

```powershell
python -m chaseos agent-bus codex-daemon --interval 30 --executor codex
```

For smoke tests, use:

```powershell
python -m chaseos agent-bus codex-daemon --once --executor mock --json
```

The daemon claims tasks addressed to `Codex`, executes through the bounded
adapter packet, writes artifacts under `runtime/adapters/codex/runs/`, and emits
standard agent-bus events back to ChaseOS.

## Standing boundary

Do not directly mutate Pulse memory, Personal Map, R&D truth-state records, or
other governed core state unless the operator explicitly asks and the change has
implementation evidence. Prefer patch artifacts and reviewable diffs.

For Pulse work, prioritize Phase A/B first: docs/spec scaffolding, runtime
folders, strict schemas, JSON + Markdown deck output, and no UI dependency until
those foundations are proven.
