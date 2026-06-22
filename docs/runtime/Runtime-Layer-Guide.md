# runtime/ — ChaseOS Gate Runtime Layer

> Machine-readable policy files, adapter manifests, and the Gate entrypoint.
> This folder is the enforcement substrate for the ChaseOS Gate.
> Human-readable policy docs live in markdown. This folder is where those policies become code.

---

## What This Folder Contains

```
runtime/
├── README.md                       ← this file
├── chaseos_gate.py                 ← Gate entrypoint stub
└── policy/
    ├── protected_files.yaml        ← machine-readable protected file list (mirrors Permission-Matrix.md §2)
    ├── tasks/
    │   ├── ingestion.yaml          ← task profile: ingestion pass
    │   └── docs_pass.yaml          ← task profile: architecture/docs pass
    └── adapters/
        ├── claude.yaml             ← adapter manifest: Claude Code (Anthropic harness)
        ├── hermes.yaml             ← adapter manifest: Hermes shadow runtime adapter
        ├── openai.yaml             ← adapter manifest: OpenAI surfaces
        ├── local_oss.yaml          ← adapter manifest: Local/OSS adapters
        └── n8n.yaml                ← adapter manifest: n8n workflow runtime
```

---

## Policy Files

### `protected_files.yaml`
Machine-readable list of all protected files in ChaseOS. This is the authoritative source for the `protected_write_guard.py` hook. It must stay in sync with `06_AGENTS/Permission-Matrix.md` Section 2. If they diverge, both must be updated.

### `gateway_allowlists.json`
Machine-readable allowlists for gateway write targets, task types, external APIs, control-plane transports, and credential-reference forms. Gateway-sensitive mutations fail closed unless they match this file and the relevant adapter/runtime operation policy.

Credential-bearing setup and gateway paths must store references only: environment variable names, keychain/local-secret references, or template placeholders. Raw secret values are blocked before `runtime/setup_state.json` is written.

### `tasks/`
Task profiles define required reads, allowed write targets, and approval mode for each session type. Task profiles allow the Gate to validate session context before execution begins (Phase 7+).

### `adapters/`
Adapter manifests (YAML) that declare what each execution adapter is allowed to do. These are the machine-readable counterparts to the human-readable adapter docs (CLAUDE.md, OPENAI.md, etc.).

Standard: `06_AGENTS/Adapter-Manifest-Standard.md`

---

## Gate Entrypoint

`runtime/chaseos_gate.py` is the Python stub that will grow into the full ChaseOS Gate as the system matures. In its current form it:
- Loads and validates adapter manifests
- Provides a policy-check interface (`check_write_permission`, `check_task_type`, etc.)
- Enforces gateway allowlists for writes, task types, external APIs, control-plane transports, and credential references
- Can be imported by hook scripts for shared policy logic

---

## AOR Workflow Layer

`runtime/aor/` and `runtime/workflows/registry/` hold the Phase 9 Autonomous Operator Runtime workflow substrate.

Developer Co-Development Mode is registered here as `developer_repo_explain_shadow`:
- ChaseOS-owned feature; adapter-capable, not adapter-owned
- Manifest: `runtime/workflows/registry/developer_repo_explain_shadow.yaml`
- Role card: `06_AGENTS/role-cards/developer-copilot-shadow.yaml`
- Handler: `runtime/aor/developer_shadow.py`
- Task type: `developer-copilot-shadow` in `runtime/aor/task_type_table.yaml`
- Output scope: draft artifacts in `docs/framework-logs/Developer-Briefs/`, audit in `docs/framework-logs/Agent-Activity/`, build log in `docs/framework-logs/Build-Logs/`, archive note in `99_ARCHIVE/Documentation-History/`

This runtime registration does not make Developer Co-Development Mode a provider or harness feature. Adapters execute the workflow under declared permission scope; ChaseOS owns the feature identity.

---

## Acquisition Runtime Layer

`runtime/acquisition/` holds the first Phase 9 Acquisition + Normalization implementation foothold.

Registered workflow:
- Workflow id: `source_pack_builder`
- Manifest: `runtime/workflows/registry/source_pack_builder.yaml`
- Role card: `06_AGENTS/role-cards/source-pack-builder.yaml`
- Handler: `runtime/acquisition/source_pack_builder.py`
- Task type: `source-pack-builder` in `runtime/aor/task_type_table.yaml`
- Output scope: `runtime/acquisition/packs/` or `docs/framework-logs/Acquisition-Packs/`

The first foothold produces only:
- `source_packet`
- `normalized_source_pack`
- `briefing_ready_input_set`

It does not add MCP surfaces, native cron, live browser automation, delivery adapters, memory candidates, action packets, outcome scoring, or canonical writeback.

---

## Runtime Navigation Memory Layer

`runtime/memory/nav/` holds the first machine-readable implementation foothold for per-runtime navigation overlays.

Seeded artifacts:
- `runtime/memory/nav/_schema.json` — schema for runtime navigation overlays
- `runtime/memory/nav/hermes/nav-map.json` — Hermes runtime navigation seed
- `runtime/memory/nav/openclaw/nav-map.json` — OpenClaw runtime navigation seed
- `runtime/memory/nav/Runtime-Navigation-Folder-Guide.md` — markdown/standalone bridge notes

These files are subordinate to:
- `06_AGENTS/Vault-Map.md` (shared structural routing truth)
- `06_AGENTS/Runtime-Navigation-Map.md` (architecture)
- `06_AGENTS/Hermes-Runtime-Profile.md` and `06_AGENTS/OpenClaw-Runtime-Profile.md` (human-readable runtime profiles)

The purpose of this layer is to preserve runtime-specific routes in a way that remains compatible with both:
1. the current Obsidian markdown + index-note structure, and
2. the future standalone ChaseOS representation.

---

## Runtime Bootstrap and User Attachment Layer

`runtime/bindings/` holds the first machine-readable bootstrap and detachable user-attachment contract layer.

Seeded artifacts:
- `runtime/bindings/Runtime-Bindings-Folder-Guide.md` — bootstrap/attachment contract overview
- `runtime/bindings/runtime-bootstrap.schema.json` — startup contract schema
- `runtime/bindings/user-attachment.schema.json` — detachable user attachment schema
- `runtime/bindings/openclaw.bootstrap.example.json` — example runtime bootstrap record
- `runtime/bindings/user-attachment.example.json` — example attachment record

This layer is subordinate to:
- `06_AGENTS/Portable-Runtime-Identity-and-User-Binding.md`
- `06_AGENTS/Agent-Control-Plane.md`
- `06_AGENTS/Permission-Matrix.md`
- adapter manifests in `runtime/policy/adapters/`

Its purpose is to make runtime startup posture portable across Windows, WSL, Linux, and future execution surfaces without fusing personal user attachment directly into framework-safe runtime identity.

---

## Runtime Coordination Bus Layer

`runtime/agent-bus-example/` holds the first durable dual-runtime coordination substrate for Hermes ↔ OpenClaw work.

Seeded artifacts:
- `runtime/agent-bus-example/Agent-Bus-Folder-Guide.md` — coordination substrate overview
- `runtime/agent-bus-example/task-packet.schema.json` — machine task packet schema
- `runtime/agent-bus-example/event.schema.json` — immutable event schema
- `runtime/agent-bus-example/heartbeat.schema.json` — runtime heartbeat schema
- `runtime/agent-bus-example/sqlite_schema.sql` — durable SQLite schema
- `runtime/agent-bus-example/examples/` — example packets
- `runtime/openclaw/coordination_bridge.md` — OpenClaw bridge instructions
- `runtime/hermes/agents.md` + `runtime/hermes/coordination_bridge.md` — Hermes bridge instructions

This layer is subordinate to:
- `06_AGENTS/Runtime-InterAgent-Coordination-Bus.md`
- `06_AGENTS/ChaseOS-Discord-Control-Plane.md`
- `HERMES.md`
- `OPENCLAW.md`

Its purpose is to keep runtime-to-runtime machine coordination durable, inspectable, and separate from Discord free-chat while preserving AOR, role-card, and adapter-boundary enforcement.

---

## Sync Requirements

| Policy file | Must match | When to update |
|-------------|-----------|----------------|
| `gateway_allowlists.json` | `06_AGENTS/ChaseOS-Gate.md` + `runtime/SETUP-README.md` + `runtime/COMMANDS.md` | When gateway write targets, task types, external APIs, transports, or credential-reference rules change |
| `protected_files.yaml` | `Permission-Matrix.md` Section 2 | When protected-file list changes |
| `adapters/claude.yaml` | `CLAUDE.md` + `Hook-Patterns.md` | When Claude adapter scope changes |
| `adapters/hermes.yaml` | `HERMES.md` + `config/chaseos-example/hermes_config.yaml` | When Hermes shadow/runtime scope changes |
| `adapters/openai.yaml` | `OPENAI.md` | When OpenAI adapter activates or changes |
| `adapters/local_oss.yaml` | `LOCAL-OSS.md` | When Local/OSS adapter activates |
| `adapters/n8n.yaml` | `N8N.md` | When n8n deploys or scope changes |
| `memory/nav/*/nav-map.json` | `06_AGENTS/Runtime-Navigation-Map.md` + runtime profile docs | When runtime routes, trust zones, or escalation boundaries change |
| `bindings/runtime-bootstrap.schema.json` | `06_AGENTS/Portable-Runtime-Identity-and-User-Binding.md` | When bootstrap contract structure changes |
| `bindings/user-attachment.schema.json` | `06_AGENTS/Portable-Runtime-Identity-and-User-Binding.md` + `CORE_MANIFEST.md` | When detachable user-binding structure changes |
| `agent_bus/*.schema.json` + `agent_bus/sqlite_schema.sql` | `06_AGENTS/Runtime-InterAgent-Coordination-Bus.md` | When coordination packet/state rules change |
| `browser_registry/allowed_origins.yaml` | `06_AGENTS/Browser-Autonomy-Policy.md` + browser role-card boundaries | When approved browser origins or source classes change |
| `browser_registry/task_classes.yaml` | `06_AGENTS/Browser-Task-Patterns.md` + browser role-card boundaries | When bounded browser task classes change |
| `SiteWorkflow/registry/**/*.json` | `06_AGENTS/ChaseOS-SiteWorkflow.md` + `runtime/SiteWorkflow/README.md` + `runtime/COMMANDS.md` | When SiteWorkflow site profiles, provider profiles, workflow manifests, or Site Skill cards change |
| `tasks/ingestion.yaml` | `Session-Prompt-Patterns.md` Pattern 5 | When ingestion pattern changes |
| `tasks/docs_pass.yaml` | `Session-Prompt-Patterns.md` Pattern 1 | When docs pass pattern changes |

---

## What Belongs Here vs Vault

| Type | Lives in | Why |
|------|----------|-----|
| YAML policy and manifests | `runtime/` | Machine-readable; enforcement substrate |
| Python hook scripts | `.claude/hooks/` | Claude Code lifecycle scripts |
| Human-readable adapter docs | vault root / `06_AGENTS/` | Policy and architecture narrative |
| Gate architecture doc | `06_AGENTS/ChaseOS-Gate.md` | Framework-level policy doc |

---

*ChaseOS Gate: `06_AGENTS/ChaseOS-Gate.md` · Manifest standard: `06_AGENTS/Adapter-Manifest-Standard.md` · Permission Matrix: `06_AGENTS/Permission-Matrix.md`*



## Related
- [[Projects-Hub]]


*Graph links auto-wired by vault_hygiene (2026-04-24): [[SIC-Provider-Adapter-Standard]] . [[research_digest]] . [[runtime_observation]] . [[source_package_schema]] . [[user_morning_thesis]] . [[workspace_schema]]*


*Graph links: [[OpenClaw-Runtime-Profile]]*
