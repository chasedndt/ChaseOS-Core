---
title: ChaseOS Studio Launch Instructions
date: 2026-05-06
runtime: Codex
status: ACTIVE / OPERATOR LAUNCH REFERENCE
---

# ChaseOS Studio Launch Instructions

Use this note when an operator wants to see the Studio interface locally.

## Native Studio Shell

The canonical product lane is the native PyWebView shell:

```powershell
cd C:\Users\chaseos\Documents\chaseos_obsidian
python -m chaseos studio shell
```

Optional explicit vault root:

```powershell
python -m chaseos studio shell --vault-root C:\Users\chaseos\Documents\chaseos_obsidian
```

Native shell does not use an operator-selected browser port. It opens a desktop window.

## Localhost Compatibility Harness

Use the localhost harness when you want to open Studio in a normal browser on your own port:

```powershell
cd C:\Users\chaseos\Documents\chaseos_obsidian
python -m chaseos studio desktop-shell-app --host 127.0.0.1 --port 8788
```

Then open:

```text
http://127.0.0.1:8788/
```

Useful routes:

```text
http://127.0.0.1:8788/#graph-view
http://127.0.0.1:8788/#node-inspector
http://127.0.0.1:8788/#browser-runtime
http://127.0.0.1:8788/#workspace-entry
http://127.0.0.1:8788/#settings
http://127.0.0.1:8788/#approval-center
http://127.0.0.1:8788/#runtime-cockpit
```

Any free loopback port can be used. If `8788` is busy, choose another port such as `8789`, `8790`, or `8872`.

## Bounded Checks

Preview the harness plan without starting the server:

```powershell
python -m chaseos studio desktop-shell-app --host 127.0.0.1 --port 8788 --dry-run --json
```

Run a bounded smoke test that starts an internally owned server, checks routes, and stops it:

```powershell
python -m chaseos studio desktop-shell-app --host 127.0.0.1 --port 8788 --smoke --use-requested-port --json
```

Run the harness for a fixed time and then stop automatically:

```powershell
python -m chaseos studio desktop-shell-app --host 127.0.0.1 --port 8788 --serve-seconds 120
```

## Studio Chat Schedule Manual Test Harness

Use this bounded harness when you specifically want to manually click through the Studio Chat schedule controls in a normal browser. It is not the full native Studio shell; it is a loopback-only test surface backed by the same governed `StudioAPI` methods as the native Chat page.

```powershell
cd C:\Users\chaseos\Documents\chaseos_obsidian
python -m runtime.studio.phase11_chat_schedule_manual_test_app --host 127.0.0.1 --port 8791 --serve-seconds 1800
```

Then open:

```text
http://127.0.0.1:8791/
```

Preflight without starting the manual UI:

```powershell
python -m runtime.studio.phase11_chat_schedule_manual_test_app --host 127.0.0.1 --port 8791 --dry-run --json
```

Run the bounded route smoke test:

```powershell
python -m runtime.studio.phase11_chat_schedule_manual_test_app --host 127.0.0.1 --smoke --json
```

Manual sequence:

1. Click `Preview Proposal`.
2. If the preview is acceptable, click `Queue Proposal`.
3. Review the returned approval id and digest, then click `Consume Proposal`.
4. Click `Write Intent` only when the disabled local schedule intent should be written to `runtime/schedules/`.
5. Use `Preview Activation`, `Queue Activation`, and `Activate` only when the local ChaseOS schedule should become enabled.
6. Use `Preview Export`, `Queue Export`, and `Write Export Packet` only when the local adapter export packet should be written under `runtime/studio/chat/schedule-adapter-exports/`.

This harness does not render credential fields and rejects secret-like input strings. It does not mutate external scheduler state, OpenClaw/Hermes cron files, Discord, providers, Agent Bus tasks, runtime dispatch, workflow dispatch, or credential stores.

Check whether a port is already in use:

```powershell
Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 8788 -ErrorAction SilentlyContinue
```

## Boundary

- `chaseos studio shell` is the product lane.
- `chaseos studio desktop-shell-app` is a localhost compatibility and QA harness.
- The harness is loopback-only and read-only.
- Do not use public hosts or public tunnels for Studio.
- Do not kill unrelated port listeners unless you know they are stale Studio harness processes.


## Graph Hygiene Governance Links

*Auto-wired by vault_hygiene (2026-05-06): [[06_AGENTS/Agent-Control-Plane|Agent-Control-Plane]] . [[06_AGENTS/Vault-Map|Vault-Map]]*
