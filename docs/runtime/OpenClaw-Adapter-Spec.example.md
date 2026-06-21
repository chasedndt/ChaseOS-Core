# OpenClaw Adapter Spec Example

## Adapter Identity

- Runtime name: OpenClaw
- Adapter type: ChaseOS runtime worker
- Authority model: fail closed unless task packet and approval state allow action

## Required Inputs

- Runtime profile.
- Capability manifest.
- Agent Bus task packet.
- Approval artifact when the task requires write, host, provider, browser, Discord delivery, or external authority.

## Required Outputs

- Structured result packet.
- Status or blocked reason.
- Evidence path for approved writes.
- No credential values.

## Public-Safe Configuration

```yaml
runtime: OpenClaw
capabilities_path: "runtime/openclaw/capabilities.yaml"
profile_path: "06_AGENTS/OpenClaw-Runtime-Profile.md"
credential_refs:
  provider: "env:OPENCLAW_PROVIDER_CREDENTIAL_REF"
  discord_bot: "env:DISCORD_BOT_CREDENTIAL_REF"
```

## Fail-Closed Cases

- Missing task packet.
- Unsupported task type.
- Missing approval for gated action.
- Live Discord IDs in public config.
- Target path outside the declared workspace.
