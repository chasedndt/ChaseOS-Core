# Workflow Pack Standard

A workflow pack is a reusable, governed unit of work.

## Required sections

- purpose;
- inputs;
- outputs;
- roles;
- permission ceiling;
- approval requirements;
- tools/commands;
- templates;
- verification;
- rollback/failure behavior;
- audit/writeback path.

## Status levels

- `docs_only` — described but not executable.
- `dry_run` — executable without external side effects.
- `verified_local` — proven in local controlled environment.
- `approved_live` — allowed to perform declared live action after approval.
