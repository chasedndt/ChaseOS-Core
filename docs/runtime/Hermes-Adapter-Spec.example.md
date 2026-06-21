# Hermes Adapter Spec Example

## Adapter Identity

- Runtime name: Hermes
- Adapter type: ChaseOS runtime worker
- Authority model: fail closed unless task packet and approval state allow action

## Required Inputs

- Runtime profile.
- Capability manifest.
- Agent Bus task packet.
- Approval artifact when the task requires write, host, provider, browser, or external authority.

## Required Outputs

- Structured result packet.
- Status or blocked reason.
- Evidence path for any approved write.
- No credential values.

## Public-Safe Configuration

```yaml
runtime: Hermes
capabilities_path: "runtime/hermes/capabilities.yaml"
profile_path: "06_AGENTS/Hermes-Runtime-Profile.md"
credential_refs:
  provider_key: "env:HERMES_API_KEY"
```

## Fail-Closed Cases

- Missing task packet.
- Unsupported task type.
- Missing approval for a gated action.
- Credential value requested in task context.
- Target path outside the declared workspace.

