# ChaseOS Gate

The Gate is the approval and boundary model for sensitive writes.

## Gate Responsibilities

- Validate requested actions.
- Confirm authority and target paths.
- Require approval for durable mutation.
- Produce evidence for accepted and blocked actions.
- Fail closed when scope is unclear.

## Default Posture

Read and preview actions are safer than writes. Writes that affect canonical truth, runtime state, external systems, or host configuration require explicit approval.
