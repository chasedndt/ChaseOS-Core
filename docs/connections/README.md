# ChaseOS Connections

ChaseOS Connections are first-class local-first OS resources for provider status, permission profiles, capability discovery, sync/audit state, and provenance.

This Core foothold intentionally starts with safe local registry primitives:

- provider manifests under `runtime/connections/manifests/`
- typed manifest/capability contracts in `runtime/connections/models.py`
- a local SQLite registry in `.chaseos/connections.db`
- approval-first defaults for every write or external-egress capability

Bundled alpha/experimental manifests now cover the architecture graph's first provider set:

- `discord` — Gateway/local control-plane provider.
- `telegram` — Bot API long polling first, webhook optional.
- `slack` — OAuth/Socket Mode workspace colleague provider.
- `whatsapp_business_cloud` — official Cloud API, webhook/relay-required, compliance-heavy.
- `whatsapp_personal_lab` — owner-only local bridge lab connector.
- `imessage_mac` — local Mac bridge over Messages.app/chat.db snapshots/automation.
- `github` — repository intelligence and approval-gated write actions.
- `local_files` — allowlisted local filesystem source/provider.

## Safe defaults

- New providers default to `read_only`.
- Write and external-egress tools are not enabled by default.
- Personal/private sources are opt-in.
- Ambient mode is off by default.
- Tool invocation logging is part of the schema.
- Provider tokens are not stored by this registry package.

## CLI

```bash
python -m runtime.cli.core_main connections providers --json
python -m runtime.cli.core_main connections init --vault-root /path/to/vault --json
python -m runtime.cli.core_main connections list --vault-root /path/to/vault --json
python -m runtime.cli.core_main connections seed local_files --vault-root /path/to/vault --json
```

See `docs/chase_os_current_architecture_map.md` for the Phase 0 discovery map and next build order.
