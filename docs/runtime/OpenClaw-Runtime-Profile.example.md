# OpenClaw Runtime Profile Example

## Runtime

- Name: OpenClaw
- Execution surface: runtime worker
- Access mode: bounded adapter
- Authority: bounded executor only when explicitly approved
- Host model: deployment-specific

## Role

OpenClaw can act as a ChaseOS runtime lane for scheduled execution, runtime task handling, and Discord/control-plane integration. A public profile must remain generic and must not include private host paths, tokens, or live channel IDs.

## Capabilities

- Read approved task packets.
- Claim allowed Agent Bus tasks.
- Execute approved local workflows.
- Report runtime health and task status.
- Participate in Discord delivery if configured.

## Forbidden By Default

- Credential value access.
- Unapproved canonical writeback.
- Unapproved host mutation.
- Unapproved browser or external account action.
- Unreviewed Discord command execution.

## Local Configuration Placeholders

```yaml
runtime_id: "openclaw"
provider_ref: "env:OPENCLAW_PROVIDER"
model_ref: "env:OPENCLAW_MODEL"
credential_ref: "env:OPENCLAW_API_KEY"
vault_root_ref: "env:CHASEOS_VAULT"
```

## Public Release Note

Do not publish local dashboard ports, host startup paths, account names, Discord IDs, or live runtime logs unless they are synthetic examples.

