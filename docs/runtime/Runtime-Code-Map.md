# Runtime Code Map

This map explains the source-safe runtime code exported with ChaseOS Core.

## Included Code Areas

- `runtime/core_export/` — manifest loading, sanitizer, scanner, dry-run exporter, and report verification support.
- `runtime/cli/` selected surfaces — public CLI entrypoints and JSON contract helpers.
- `runtime/subagents/` — bounded subagent preset routing, approval packets, policies, and telemetry helpers.
- Selected tests that prove public export and CLI contracts.

## Excluded Code Areas by Default

- Live runtime queues and memory.
- Local lifecycle launchers.
- Provider credentials and connector config.
- Machine-specific Studio proof scripts and screenshots.
- Personal workflow outputs.
