# Agent Security Model

The security model keeps runtime capability separate from runtime authority.

## Rules

- Capability does not imply permission.
- External access must be declared.
- Durable writes require bounded targets.
- Generated output must remain marked until reviewed.
- Runtime failures should produce evidence instead of silent mutation.

## Sensitive Surfaces

- Credential values.
- Canonical system truth.
- Private memory.
- Runtime state.
- Host configuration.
