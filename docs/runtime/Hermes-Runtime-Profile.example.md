# Hermes Runtime Profile Example

## Runtime

- Name: Hermes
- Execution surface: runtime worker
- Access mode: bounded adapter
- Authority: proposer / bounded executor when explicitly approved
- Host model: deployment-specific

## Role

Hermes can act as a ChaseOS runtime lane for synthesis, review, shadow operation, and approved task execution. A public profile must describe boundaries, not private credentials or local machine paths.

## Capabilities

- Read approved task packets.
- Produce structured results.
- Participate in Agent Bus coordination.
- Run approved runtime workflows within declared authority.
- Report status to local ChaseOS surfaces.

## Forbidden By Default

- Reading credential values.
- Writing canonical memory without approval.
- Mutating host startup state without approval.
- Publishing externally.
- Bypassing ChaseOS Gate or approval artifacts.

## Local Configuration Placeholders

```yaml
runtime_id: "hermes"
provider_ref: "env:HERMES_PROVIDER"
model_ref: "env:HERMES_MODEL"
credential_ref: "env:HERMES_API_KEY"
vault_root_ref: "env:CHASEOS_VAULT"
```

## Public Release Note

Do not publish real provider names, keys, host paths, WSL usernames, local ports, or run logs unless they are intentionally public examples.

