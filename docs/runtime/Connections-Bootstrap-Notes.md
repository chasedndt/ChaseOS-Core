# Connections Bootstrap — Engineering Notes

**Date:** 2026-06-26  
**Scope:** ChaseOS Core repository inspection for the Connections + channel colleague architecture handover.  
**Status:** Phase 0 discovery plus a minimal Core-safe Connections registry foothold.

> This is a point-in-time engineering handover note, kept for build history. For the
> current, curated picture, see [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md).

## Repository identity

- `README.md` states ChaseOS Core is a public local-first framework for governed memory, source intelligence, agent boundaries, approval workflows, runtime discipline, and evidence-first writeback.
- `pyproject.toml` shows a Python 3.11 package named `chaseos-core` with a lean CLI entrypoint: `chaseos = runtime.cli.core_main:main`.
- Core intentionally excludes private runtime logs, credentials, provider deployment state, and machine-local paths.

## Entrypoints

| Surface | Live path | Notes |
|---|---|---|
| Lean Core CLI | `runtime/cli/core_main.py` | Argparse CLI for version, doctor, commerce, run, capture, schedule, and now Connections registry commands. |
| AOR workflow runner | `runtime/aor/*` | Bounded workflow execution layer used by `chaseos run`. |
| MCP payload builder | `runtime/adapters/openai/responses_mcp_payload.py` | Dry-run Responses API remote-MCP payload builder; requires approval and blocks forbidden tool/data classes. |

## Existing services located

### Discord / control-plane surface

- Public-safe Discord docs live under `docs/agents/Discord-Control-Plane.md`, `docs/agents/Discord-Command-Envelope-Schema.md`, `docs/agents/Discord-Channel-Registry.example.md`, and related templates.
- The docs define Discord as an operator-facing transport, not canonical memory or approval authority.
- In this Core repo, there is no live Discord bot token or deployment-specific channel binding. That matches Core's public-safe boundary.

### Memory and graph

- `runtime/memory/inspector.py` is read-only Layer C/D runtime memory inspection. It covers runtime profiles, identity ledgers, nav maps, repair memory, scorecards, and task-local memory roots.
- `runtime/graph/artifact.py` defines the canonical graph snapshot model with deterministic node/edge IDs and provenance.
- `runtime/graph/query.py` provides read-only graph search, node/community inspection, shortest path, and graph-first source narrowing.
- The current graph substrate is structural/source-map oriented, not yet the temporal connected-source fact graph described in the Connections handover.

### Capture/source ingestion

- `runtime/capture/connectors/__init__.py` describes Phase 8 connectors as source-specific `ContentPacket` producers.
- Existing capture connectors are ingestion helpers, not a permissioned first-class Connections registry with status, capabilities, auth state, sync jobs, provenance, and audit.

### Approval/governance

- `runtime/operator_surface/approvals.py` provides in-memory approval request/response models for bounded operator execution.
- `runtime/osril/approvals.py`, `runtime/subagents/approval_packet.py`, and governance docs define broader approval surfaces.
- Connections write/egress actions should reuse this approval posture rather than bypass it.

### MCP

- `runtime/adapters/openai/responses_mcp_payload.py` is a safe dry-run MCP payload builder, not a full MCP client registry.
- Docs under `docs/agents/` describe MCP usage, guardrails, server design, data contracts, and audit policy.
- The next MCP Gateway phase should build from these policy constraints: approval required, forbidden tool names blocked, remote server data-sharing warning, no live call in dry-run builder.

## New Connections foothold added in this pass

| Path | Purpose |
|---|---|
| `runtime/connections/__init__.py` | Public package exports for manifests and store helpers. |
| `runtime/connections/models.py` | Typed dataclasses and policy vocabularies for manifests/capabilities. |
| `runtime/connections/manifests.py` | YAML provider manifest loader and validation. |
| `runtime/connections/store.py` | Local-first SQLite schema initializer and placeholder connection seeding. |
| `runtime/connections/manifests/local_files.yaml` | Read-only-first local files manifest with diff/write approval gates. |
| `runtime/connections/manifests/github.yaml` | Read-only-first GitHub manifest with create issue/PR gated. |
| `runtime/connections/manifests/discord.yaml` | Discord as Gateway/local control plane and optional data source, with egress gated. |
| `runtime/connections/manifests/telegram.yaml` | Telegram Bot API long-polling-first manifest; webhook optional and egress gated. |
| `runtime/connections/manifests/slack.yaml` | Slack OAuth/Socket Mode workspace-colleague manifest with send gated. |
| `runtime/connections/manifests/whatsapp_business_cloud.yaml` | Official WhatsApp Cloud API manifest; public ingress relay/webhook and policy constraints recorded. |
| `runtime/connections/manifests/whatsapp_personal_lab.yaml` | Owner-only local WhatsApp bridge lab manifest; production-safe=false and sends approval-gated. |
| `runtime/connections/manifests/imessage_mac.yaml` | Local Mac bridge manifest for Messages.app/chat.db snapshots, watcher cursor, normalizer, and draft-first sender. |
| `tests/test_connections_registry.py` | Unit tests for manifest safety defaults and SQLite schema/seed behavior. |

## CLI additions

`runtime/cli/core_main.py` now exposes a Core-safe `connections` command group:

```bash
python -m runtime.cli.core_main connections providers --json
python -m runtime.cli.core_main connections init --vault-root /path/to/vault --json
python -m runtime.cli.core_main connections list --vault-root /path/to/vault --json
python -m runtime.cli.core_main connections seed local_files --vault-root /path/to/vault --json
```

These commands do **not** authenticate providers, fetch private data, send messages, or enable ambient agents. They only expose manifests, initialize local schema, and seed disconnected placeholder connection rows.

## Storage

The first registry DB path is:

```text
<vault-root>/.chaseos/connections.db
```

The schema includes the handover's explicit v1 tables:

- `connections`
- `connection_capabilities`
- `sync_jobs`
- `source_items`
- `entities`
- `relationships`
- `memories`
- `tool_invocations`
- `approvals`
- `connection_registry_meta`

## Current limitations

1. No OAuth/device flow is implemented yet.
2. No provider adapter invokes live APIs yet.
3. No MCP client registry or tool invocation gateway exists yet.
4. No operator UI surface is wired in Core; this pass is CLI/schema/package foothold only.
5. No source ingestion, embeddings, or temporal fact extraction is implemented in the new package yet.
6. The temporal context graph described in the handover is not yet merged with the existing structural graph substrate.

## Recommended next implementation order

1. Add connection event/audit append helpers around `tool_invocations` and connection lifecycle changes.
2. Add local-files adapter in read-only mode using explicit allowlisted roots.
3. Add a read-only rendering surface for `connections list` and provider cards.
4. Add MCP Gateway registry with read-only tool filtering and approval-gated write tools.
5. Add GitHub read-only connector using official MCP/GitHub App/PAT policy profile.
6. Add per-channel `Channel Colleague` records only after namespace, retention, and source-leak guards are enforced.
