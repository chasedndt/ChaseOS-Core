# Codex Bus Adapter

Codex joins ChaseOS as a worker on the agent bus, not as the bus itself and not
as a direct writer to Pulse memory/core state.

## Contract

- Bus name: `Codex`
- Retained personal runtime name: `Axiom-Codex`
- Legacy personal runtime alias: `Codex-ChaseOS-Worker`
- Runtime capability manifest: `runtime/codex/capabilities.yaml`
- Adapter policy manifest: `runtime/policy/adapters/codex.yaml`
- Runtime profile: `06_AGENTS/Codex-Runtime-Profile.md`
- Input packet schema: `runtime/adapters/codex/codex-task.schema.json`
- Result schema: `runtime/adapters/codex/codex-result.schema.json`

Codex may handle:

- `code.review`
- `code.patch`
- `repo.inspect`
- `test.run`

Codex should return one of:

- `proposal`
- `patch`
- `risk`
- `blocked`
- `complete`

## Boundary

Codex must not directly mutate ChaseOS/Pulse memory, Personal Map, R&D truth
state, or runtime governance files. It should return patches or artifacts for
ChaseOS/OpenClaw review. ChaseOS/OpenClaw remains the arbiter/writer.

## Live daemon

Check readiness:

```powershell
python -m chaseos agent-bus codex-daemon --readiness --json
```

This path requires the **Codex CLI**, not only Codex chat/app access. The daemon
executes bounded bus packets through a local `codex exec` subprocess.

The live subprocess path is tuned for the current ChaseOS vault shape:

- the daemon resolves the retained runtime instance name from `runtime/codex/capabilities.yaml` and publishes/claims as `Axiom-Codex` when that manifest is present;
- it passes `--skip-git-repo-check` because this vault may not be a Git repo;
- it passes `--ephemeral` so daemon runs do not depend on long-lived Codex session persistence;
- it captures stdout/stderr as UTF-8 with replacement to avoid Windows console decode failures;
- when a task packet has `allowed_write_paths: []`, the daemon runs nested Codex with `--sandbox read-only` and the prompt treats that as a hard no-write instruction, including no build-log/daily/archive/index writeback.
- when a task packet has non-empty `allowed_write_paths`, the daemon runs nested Codex with `--sandbox workspace-write`, sets `--cd` to the first repo-confined declared write root, and passes additional repo-confined roots with `--add-dir`; paths outside the repo root are not granted.
- the live subprocess receives the bounded prompt through stdin with `codex exec -`, avoiding Windows multiline argument truncation and preventing parent-process stdin context from being appended.
- the bounded task packet is embedded in that stdin prompt and also written to `codex-task-packet.json`, so simple read-only tasks can answer from the prompt without using a shell command just to discover their request.
- when a task packet has `allow_shell_commands: false`, the daemon does not start the live Codex subprocess. It returns a blocked adapter result before spawning because this adapter cannot structurally disable nested shell-tool use inside a launched Codex CLI process.
- when a task packet has `allow_live_subprocess: false`, the daemon does not resolve or start the Codex CLI at all. This field can now be supplied from Agent Bus task-level `execution_constraints`.

Create a constrained task from an ingress/control surface:

```powershell
python -m chaseos agent-bus task create --sender OpenClaw --to Codex --request "Inspect without live execution" --expected-output "Blocked or proposal artifact" --no-shell-commands --no-live-subprocess --write-policy none --json
```

Publish or refresh Codex liveness:

```powershell
python -m chaseos agent-bus heartbeat --runtime Codex --status idle --health ok --runtime-instance-id Axiom-Codex --heartbeat-scope instance --control-surface codex-cli --control-surface-key codex-bus --json
```

If `codex` is not on PATH, install/sign in to the Codex CLI, set
`CHASEOS_CODEX_BINARY`, pass `--codex-binary`, or copy
`codex-daemon.config.example.json` to `codex-daemon.config.json` and set
`codex_binary`.

Quick CLI check:

```powershell
codex --version
```

Run one safe smoke cycle with the deterministic executor:

```powershell
python -m chaseos agent-bus codex-daemon --once --executor mock --json
```

Run Codex as a polling worker with the local Codex CLI:

```powershell
python -m chaseos agent-bus codex-daemon --interval 30 --executor codex
```

Run a no-shell guarded daemon pass:

```powershell
python -m chaseos agent-bus codex-daemon --once --executor codex --no-shell-commands --json
```

The daemon claims tasks addressed to `Codex`, writes run artifacts under
`runtime/adapters/codex/runs/`, and maps Codex adapter results back to standard
agent-bus events (`result_attached`, `blocked`, `completed`).

The daemon keeps `Codex` as the bus/runtime label while carrying the retained
personal runtime instance ID `Axiom-Codex` into readiness output, watch
heartbeats, and claimed task ownership.

Task-level `execution_constraints` are stricter than daemon defaults. The
daemon may narrow policy further with flags, but a task can request no shell,
no live subprocess, and no writes without requiring the operator to remember a
matching daemon flag.

## Phase A/B fit

This adapter deliberately starts with strict schemas, a capability manifest, pure
packet/result helpers, deterministic mock tests, and a live daemon boundary. The
Codex subprocess executor is isolated behind the same packet/result contract.


## Graph Hygiene Governance Links

*Auto-wired by vault_hygiene (2026-05-06): [[06_AGENTS/Agent-Control-Plane|Agent-Control-Plane]] . [[06_AGENTS/Vault-Map|Vault-Map]]*
