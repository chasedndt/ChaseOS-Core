# Codex Bus Runtime Handoff

Codex is now expected to join ChaseOS through the agent bus as runtime worker
`Codex`.

## Identity

- Bus name: `Codex`
- Retained personal runtime name: `Axiom-Codex`
- Legacy personal runtime alias: `Codex-ChaseOS-Worker`
- Capabilities: `runtime/codex/capabilities.yaml`
- Policy manifest: `runtime/policy/adapters/codex.yaml`
- Runtime profile: `06_AGENTS/Codex-Runtime-Profile.md`
- Adapter: `runtime/adapters/codex/`
- Run artifacts: `runtime/adapters/codex/runs/`

## Activation checks

Run readiness first:

```powershell
python -m chaseos agent-bus codex-daemon --readiness --json
```

If the Codex executable is not on `PATH`, either:

- set `CHASEOS_CODEX_BINARY` to the executable path, or
- copy `codex-daemon.config.example.json` to `codex-daemon.config.json` and set `codex_binary`, or
- pass `--codex-binary <path>` explicitly.

Run safe smoke test:

```powershell
python -m chaseos agent-bus codex-daemon --once --executor mock --json
```

Publish liveness:

```powershell
python -m chaseos agent-bus heartbeat --runtime Codex --status idle --health ok --runtime-instance-id Axiom-Codex --heartbeat-scope instance --control-surface codex-cli --control-surface-key codex-bus --json
```

Run as live polling daemon:

```powershell
python -m chaseos agent-bus codex-daemon --interval 30 --executor codex
```

Run a no-shell guarded one-shot pass:

```powershell
python -m chaseos agent-bus codex-daemon --once --executor codex --no-shell-commands --json
```

The live subprocess runner invokes `codex exec` with non-Git vault support and
ephemeral session mode. Captured stdout/stderr are written as UTF-8-tolerant
artifacts under `runtime/adapters/codex/runs/`. If a task packet declares
`allowed_write_paths: []`, the daemon runs nested Codex with `--sandbox
read-only`; treat it as a hard no-write packet, including no
build-log/daily/archive/index writeback from the nested Codex process.
If a task packet declares non-empty `allowed_write_paths`, the daemon starts
nested Codex with `--sandbox workspace-write`, sets `--cd` to the first
repo-confined declared write root, and grants additional repo-confined roots
with `--add-dir`. Paths outside the repo root are not granted. The daemon sends
the bounded prompt through stdin with `codex exec -`, avoiding Windows multiline
argument truncation and preventing parent-process stdin context from being
appended. The bounded task packet is embedded in the stdin prompt and also
written to `codex-task-packet.json`, so simple read-only tasks can return useful
output without running a shell command solely to discover their request.
If a task packet declares `allow_shell_commands: false`, the live subprocess is
not started at all. The daemon returns a blocked adapter result before spawn
because the current Codex CLI executor cannot structurally disable nested
shell-tool use after launch.
If a task packet declares `allow_live_subprocess: false`, the live subprocess is
also not started. This can now be driven from Agent Bus task metadata:

```powershell
python -m chaseos agent-bus task create --sender OpenClaw --to Codex --request "Inspect without live execution" --expected-output "Blocked or proposal artifact" --no-shell-commands --no-live-subprocess --write-policy none --json
```

Task-level `execution_constraints` narrow adapter behavior and do not grant
new write, shell, runtime-state, or canonical-state authority.

The daemon resolves the retained personal runtime name from
`runtime/codex/capabilities.yaml`. Current readiness, heartbeat, and task-claim
identity should show runtime `Codex` with instance `Axiom-Codex`.

## Operating boundary

Codex claims tasks addressed to `Codex`, reads a bounded task packet, and returns
reviewable artifacts. It must not directly mutate Pulse memory, Personal Map,
R&D truth-state records, or governed core runtime state unless explicitly
authorized by the operator and backed by implementation evidence.

## Result mapping

Codex adapter event kinds map back into standard ChaseOS bus events:

- `proposal` / `patch` / `risk` -> `result_attached`, task `done`
- `complete` -> `completed`, task `done`
- `blocked` -> `blocked`, task `blocked`

This keeps existing agent-bus storage and event schemas stable while preserving
Codex-specific semantics in the attached adapter result artifact.

## Future Codex chats

Future Codex sessions working on ChaseOS should treat the bus as the default
integration path. Prefer receiving/returning bounded bus packets rather than
acting as a free-floating repo mutator.


## Graph Hygiene Governance Links

*Auto-wired by vault_hygiene (2026-05-06): [[06_AGENTS/Agent-Control-Plane|Agent-Control-Plane]] . [[06_AGENTS/Vault-Map|Vault-Map]]*
