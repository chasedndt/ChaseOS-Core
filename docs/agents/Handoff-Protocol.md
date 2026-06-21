# Handoff Protocol

A handoff lets one operator/runtime continue work without inheriting unsafe authority or stale context.

## Handoff packet

Include:

- goal;
- current state;
- files touched;
- verification already run;
- blocked decisions;
- authority limits;
- next safe action.

## Rules

- Handoffs are data, not automatic approval.
- Do not transfer credentials.
- Do not treat stale chat memory as live repo truth.
- Revalidate files, Git state, and configured targets before acting.
