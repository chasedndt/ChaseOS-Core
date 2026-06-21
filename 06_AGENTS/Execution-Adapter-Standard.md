# Execution Adapter Standard

Adapters connect runtimes to ChaseOS without handing them ownership of the system.

## Adapter Fields

- Runtime name.
- Supported task types.
- Input packet schema.
- Output artifact schema.
- Allowed files or targets.
- Approval requirements.
- Evidence locations.

## Runtime Boundary

Adapters should return proposals, patches, risks, blockers, or completion evidence. System truth changes remain governed by ChaseOS.
