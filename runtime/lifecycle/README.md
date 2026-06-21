# runtime/lifecycle/ — Runtime Lifecycle Layer

> Machine-readable lifecycle records for bounded ChaseOS runtime lanes.

---

## Purpose

This folder is the future machine-readable substrate for runtime lifecycle ownership in ChaseOS.

It exists to define, per runtime:
- how the runtime is started
- how it is stopped
- how it is restarted
- how health is checked
- how coordination-watch loops should be launched from inside ChaseOS
- what platform assumptions apply
- what ownership/lifecycle mode is in effect

Health checks may use different probe kinds, for example:
- command-based probes
- lightweight HTTP probes
- future wrapper-script or service-aware probes

---

## Why This Matters

ChaseOS now has:
- runtime doctrine
- runtime bootstrap contracts
- runtime-state inspection
- CLI footholds
- lifecycle contract docs

The next missing layer is machine-readable lifecycle records.

Without this layer, runtime lifecycle remains a concept.
With this layer, it becomes an implementation substrate.

---

## Seeded Artifacts

- `runtime-lifecycle.schema.json`
- `openclaw.lifecycle.yaml`
- `hermes.lifecycle.yaml`
- `README.md`

---

## Relationship to Other Layers

This layer is subordinate to:
- `06_AGENTS/ChaseOS-Runtime-Lifecycle-Contract.md`
- `06_AGENTS/ChaseOS-CLI-Surface-Architecture.md`
- `06_AGENTS/ChaseOS-CLI-Integration-Seam.md`
- runtime-specific docs such as `OPENCLAW.md` and Hermes runtime/profile docs

This layer should not replace runtime policy, runtime state, or adapter manifests.
It is specifically for lifecycle ownership and health logic.

---

## Likely Future Use

This layer now also seeds machine-readable coordination-watch ownership through:
- `coordination_watch` blocks in runtime lifecycle records
- `runtime/lifecycle/coordination_watch.py`
- `runtime/lifecycle/coordination_watch_supervisor.py`
- `runtime/lifecycle/coordination_watch_bootstrap.py`
- promoted command surfaces:
  - `python runtime/cli.py runtime coordination-watch --runtime <id> --once|--interval N`
  - `python chaseos.py runtime coordination-watch --runtime <id> --once|--interval N`
  - `python runtime/cli.py runtime coordination-watch-supervisor --runtime <id> --action plan|status|start|stop`
  - `python chaseos.py runtime coordination-watch-supervisor --runtime <id> --action plan|status|start|stop`
  - `python runtime/cli.py runtime coordination-watch-bootstrap --runtime <id> --action plan|status|install|remove`
  - `python chaseos.py runtime coordination-watch-bootstrap --runtime <id> --action plan|status|install|remove`
  - `python runtime/cli.py runtime coordination-watch-bootstrap --runtime <id> --action apply|verify|unregister|handoff|reboot-verify|capture-success|reconcile-reboot-result|activation-report`
  - `python chaseos.py runtime coordination-watch-bootstrap --runtime <id> --action apply|verify|unregister|handoff|reboot-verify|capture-success|reconcile-reboot-result|activation-report`
  - `python chaseos.py runtime coordination-watch-bootstrap --runtime <id> --action activation-checklist`
  - `python chaseos.py runtime startup-surfaces --runtime all --json`
  - `python chaseos.py runtime startup-surface-settings --runtime <id|all> --json`
  - `python chaseos.py runtime startup-surface-toggle-plan --runtime <id> --surface <surface_id> --intent enable|disable --json`
  - `python chaseos.py runtime startup-surface-mutation-contract --runtime <id> --surface <surface_id> --intent enable|disable --json`
  - `python chaseos.py runtime startup-surface-approval-request --runtime <id> --surface <surface_id> --intent enable|disable [--gate-approval-id <id>] [--write-approval-request] --json`
  - `python chaseos.py runtime startup-surface-approval-decision --gate-approval-id <id> --decision approved|denied [--write-approval-decision] --json`
  - `python chaseos.py runtime startup-surface-executor-preflight --runtime <id> --surface <surface_id> --intent enable|disable --gate-approval-id <id> --plan-digest <sha256> --json`
  - `python chaseos.py runtime startup-surface-approval-consumption --runtime <id> --surface <surface_id> --intent enable|disable --gate-approval-id <id> --plan-digest <sha256> [--write-approval-consumption] --json`
  - `python chaseos.py runtime startup-surface-toggle --runtime <id> --surface <surface_id> --intent enable|disable --confirm`
  - `python chaseos.py studio runtime-startup-controls --runtime <id|all> --json`
  - `python chaseos.py studio runtime-startup-controls --runtime <id> --surface <surface_id> --intent enable|disable --action dry-run|toggle [--confirm-action] --json`
  - `python chaseos.py studio runtime-startup-controls-app --runtime <id|all> --dry-run --json`
  - `python chaseos.py studio runtime-startup-controls-app --runtime <id|all> --host 127.0.0.1 --port 8766`

Later commands may read from this layer to implement:

```text
chaseos runtime start <runtime>
chaseos runtime stop <runtime>
chaseos runtime restart <runtime>
chaseos runtime health <runtime>
chaseos runtime coordination-watch <runtime>
```

This layer is also the required machine-readable source for the future Studio Runtime Cockpit startup toggles. Each runtime that supports user-controlled startup/autostart should declare enough lifecycle metadata for the UI to show and mutate the feature through governed service-layer commands rather than direct host edits.

Permanent portable handoff: `06_AGENTS/Runtime-Startup-Controls-Portable-Handoff.md`. Use that file when carrying this feature into another ChaseOS user instance; it separates the reusable framework contract from this machine's Hermes/OpenClaw paths and evidence.

The read-only `startup-surfaces` report is the first Studio-facing backend contract for that requirement. It aggregates declared gateway launcher, coordination-watch supervisor, and coordination-watch bootstrap surfaces into `off`, `configured`, `registered`, `running`, `degraded`, and `proven-after-reboot` states without mutating Windows Startup folder entries, Task Scheduler entries, services, or lifecycle files.

The read-only `startup-surface-settings` report is the user/Studio settings model for these controls. It exposes which surfaces are user-manageable, the live CLI enable/disable command, dry-run commands, and the declared managed launcher profile. For Hermes gateway, it records that Windows Startup must delegate into WSL Ubuntu as user `<your-username>`, run from the ChaseOS repo path, retry while WSL comes online after logon, and write `C:\Users\<your-username>\.hermes\gateway-startup.log`.

The read-only `startup-surface-toggle-plan` command previews the enable/disable intent, target state, current proof state, service-layer mutation steps, and verification commands for one runtime surface. It is not an executor; it keeps `mutation_enabled: false` and does not call `start`, `stop`, `apply`, `unregister`, `remove`, or write host startup files.

The read-only `startup-surface-mutation-contract` command is the approval/UI contract layer after the toggle plan. It declares the Gate operation name, operator evidence requirements, write-target categories, host side-effect boundary, verification commands, audit records, and rollback plan for one runtime surface. The direct CLI executor now exists as `startup-surface-toggle --confirm`; the approval request/decision/consumption artifact lane exists, while approval-driven host mutation remains unbuilt.

The startup-surface approval artifact lane is:

```powershell
chaseos runtime startup-surface-approval-request --runtime hermes --surface gateway --intent disable --gate-approval-id <id> --write-approval-request --json
chaseos runtime startup-surface-approval-decision --gate-approval-id <id> --decision approved --write-approval-decision --json
chaseos runtime startup-surface-executor-preflight --runtime hermes --surface gateway --intent disable --gate-approval-id <id> --plan-digest <sha256> --json
chaseos runtime startup-surface-approval-consumption --runtime hermes --surface gateway --intent disable --gate-approval-id <id> --plan-digest <sha256> --write-approval-consumption --json
```

The request and decision commands write repo-local approval artifacts only. The preflight validates an approval artifact id, expected plan digest, current startup-surface state, required Gate operation, and idempotency marker path. The consumption command writes only an approval-consumption record and exact-once idempotency marker; it does not start/stop processes, register/unregister host startup, or edit Startup folder / scheduler / lifecycle state.

The mutating CLI command is:

```powershell
chaseos runtime startup-surface-toggle --runtime hermes --surface gateway --intent enable --confirm
chaseos runtime startup-surface-toggle --runtime hermes --surface gateway --intent disable --confirm
```

Use `--dry-run --json` first when inspecting the exact target files and Gate operation. For gateway surfaces, enable writes/repairs the declared target launcher and Startup-folder delegate; disable removes the declared Startup-folder delegate while leaving the managed target launcher in place for later re-enable. The command writes mutation markers and JSONL events under `runtime/lifecycle/run/startup-surface-mutations/`.

The Studio-facing CLI wrapper is:

```powershell
chaseos studio runtime-startup-controls --runtime hermes --json
chaseos studio runtime-startup-controls --runtime hermes --surface gateway --intent disable --action dry-run --json
chaseos studio runtime-startup-controls --runtime hermes --surface gateway --intent disable --action toggle --confirm-action
```

This wrapper exposes the Runtime Cockpit control model and calls the same lifecycle executor after Gate checks. It does not write host startup files directly. The localhost visual wrapper exists; broad Studio desktop integration remains unbuilt.

The localhost-only visual wrapper is:

```powershell
chaseos studio runtime-startup-controls-app --runtime hermes --dry-run --json
chaseos studio runtime-startup-controls-app --runtime hermes --host 127.0.0.1 --port 8766
```

This app renders the same Runtime Cockpit startup cards in a local loopback page and posts dry-run/live toggle attempts back through `studio runtime-startup-controls`. Live toggles still require confirmation and still route through the lifecycle executor; the app does not gain direct host-startup write authority.

Minimum future UI-facing declarations:
- runtime id and UI label
- supported startup surfaces, for example `gateway`, `coordination_watch_supervisor`, `coordination_watch_bootstrap`, or a host service binding
- startup registration kind, for example Windows Startup folder, Task Scheduler, service manager, WSL indirection, launch agent, or cron
- enable/disable or install/remove command surfaces
- status/proof command surfaces
- artifact/evidence paths for configured state, registered state, running supervisor state, heartbeat freshness, and post-reboot success proof
- host/elevation requirements and the boundary between current-session running proof and reboot/logon proof

If a future runtime does not support startup toggles, its lifecycle record should say so explicitly instead of leaving Studio to infer support.

---

## Current Boundary

This layer is no longer health-only seeded.
Current live truth now includes:
- machine-readable lifecycle records
- machine-readable `coordination_watch` ownership/config blocks for Hermes and OpenClaw
- a lifecycle-backed coordination-watch launcher under `runtime/lifecycle/coordination_watch.py`
- a lifecycle-backed coordination-watch supervision foothold under `runtime/lifecycle/coordination_watch_supervisor.py`
- machine-readable background-state/log paths plus `autostart` / `restart_policy` declarations for each runtime's coordination-watch loop
- a lifecycle-backed coordination-watch bootstrap-registration foothold under `runtime/lifecycle/coordination_watch_bootstrap.py`
- machine-readable launcher/registration-artifact paths plus registration-kind / trigger declarations for each runtime's coordination-watch autostart contract
- machine-readable bootstrap event-log paths plus latest-event visibility for audit-significant registration attempts, handoffs, and cleanup actions
- machine-readable reboot-verification script/artifact/result paths for post-registration restart/logon checks
- machine-readable success-record paths for durable post-check evidence capture once the restart/logon verification has actually been run
- machine-readable activation reports that aggregate install status, host scheduler query, supervisor state, agent-bus heartbeat freshness, success records, and reboot-verification evidence without mutating host registration
- promoted runtime command surfaces that can run one-shot or repeating bus refresh loops from inside ChaseOS
- promoted runtime command surfaces that can plan, start, inspect, and stop the bounded local background loop process from inside ChaseOS
- supervisor status verifies Windows PIDs with a `tasklist` check plus Windows API / PowerShell fallbacks, so restricted `tasklist` output does not falsely mark a live supervisor as stopped
- promoted runtime command surfaces that can plan, install, inspect, and remove bounded host-registration artifacts for startup ownership
- promoted runtime command surfaces that can attempt host registration, verify host registration presence, unregister scheduler entries through declared startup commands, generate ready-to-run elevated handoff bundles when the current shell lacks permission, emit post-registration reboot-verification bundles that also write durable observed-result JSON on the host side, reconcile that reboot-result evidence into later success capture either implicitly through `capture-success` or explicitly through `reconcile-reboot-result`, aggregate live proof with `activation-report`, and emit Agent Activity records only when startup success is actually confirmed
- read-only startup-surface report output for Studio Runtime Cockpit planning: `chaseos runtime startup-surfaces --runtime all --json`
- read-only startup-surface settings output for CLI/Studio toggle rendering: `chaseos runtime startup-surface-settings --runtime <id|all> --json`
- read-only startup-surface enable/disable plan output for Studio Runtime Cockpit confirmation flows: `chaseos runtime startup-surface-toggle-plan --runtime <id> --surface <surface_id> --intent enable|disable --json`
- read-only startup-surface mutation contract output for future Studio approval/executor wiring: `chaseos runtime startup-surface-mutation-contract --runtime <id> --surface <surface_id> --intent enable|disable --json`
- startup-surface approval request/decision/preflight/consumption artifacts for guarded UI flows; consumption writes only repo-local approval-consumption and idempotency marker artifacts
- startup-surface executor preflight output for guarded execution validation: `chaseos runtime startup-surface-executor-preflight --runtime <id> --surface <surface_id> --intent enable|disable --gate-approval-id <id> --plan-digest <sha256> --json`
- guarded CLI startup-surface enable/disable execution: `chaseos runtime startup-surface-toggle --runtime <id> --surface <surface_id> --intent enable|disable --confirm`
- Studio CLI startup controls model/action wrapper: `chaseos studio runtime-startup-controls --runtime <id|all> --json` and `chaseos studio runtime-startup-controls --runtime <id> --surface <surface_id> --intent enable|disable --action dry-run|toggle [--confirm-action]`
- localhost-only visual startup controls wrapper: `chaseos studio runtime-startup-controls-app --runtime <id|all> --host 127.0.0.1 --port 8766`

Still not built yet:
- generalized start/stop/restart execution through this layer
- broad ChaseOS Studio desktop Runtime Cockpit integration beyond the localhost wrapper
- approval-driven host mutation executor for Studio/higher-risk flows
- persisted startup-surface user preference records beyond mutation audit markers
- real elevated registration plus later restart/logon verification on this machine; `activation-report` currently makes the gap explicit as partial evidence, not proven startup persistence
- completed-task archive/retention policy for the coordination bus

---

*Read with: `runtime/LIFECYCLE-README.md` and `06_AGENTS/ChaseOS-Runtime-Lifecycle-Contract.md`*
