# CLI Architecture

The ChaseOS CLI exposes operator-visible workflow surfaces with JSON-first outputs and explicit authority boundaries.

## CLI Design Rules

- Commands should return structured JSON for automation.
- Side effects should be explicit in the command name, flags, and result metadata.
- Dry-run commands must report `writes_performed: false`.
- Gate-sensitive commands should require approval references or manual steps.
- Public Core examples should avoid machine-local paths and private runtime state.

## Public V1 CLI Focus

Core V1 should prioritize export, verification, governance, subagent, and documentation-generation surfaces before broader private-instance automation.
