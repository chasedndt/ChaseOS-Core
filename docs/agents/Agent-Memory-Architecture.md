# Agent Memory Architecture

Agent memory supports continuity, but it is not canonical truth.

## Memory layers

- Session context: temporary and incomplete.
- Runtime memory: useful preferences and operating notes.
- Logs/audits: evidence of what happened.
- Canonical knowledge: promoted through review.

## Rules

- Store durable preferences and stable environment facts only.
- Do not store stale task progress as memory.
- Revalidate live files before editing.
- Promotion into durable knowledge requires review/gate.
