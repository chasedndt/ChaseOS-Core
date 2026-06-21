# Command Reference

This public command reference is intentionally compact. Generated/private command listings should not be copied directly into Core until they pass scanner and review gates.

## Core command groups

| Group | Purpose | Public safety posture |
|---|---|---|
| `doctor` | Validate environment and config posture | read-only |
| `core-export` | Build/verify sanitized Core export previews | dry-run first |
| `capture` | Ingest external material | private instance only by default |
| `intake` | Inspect quarantine/input metadata | private instance only by default |
| `run` | Execute governed workflows | approval-gated |
| `studio` | Inspect local UI/readiness surfaces | read-only unless explicitly approved |

## Generated docs policy

Full generated command references may be released only after:

1. command contract is scanner-clean;
2. private paths and live statuses are removed;
3. examples use placeholders;
4. manual review passes.
