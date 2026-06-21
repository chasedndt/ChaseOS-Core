# CLI Quickstart

The ChaseOS CLI is the operator command surface for validation, capture, runtime inspection, export previews, and workflow control. Public Core documentation should describe safe usage patterns without exposing private command history or local paths.

## Command style

Commands should support:

- explicit source/target paths;
- JSON output for automation;
- dry-run mode for write-capable commands;
- fail-closed validation;
- no credential echoing.

## Example safe commands

```bash
chaseos --help
chaseos doctor --json
chaseos core-export build --dry-run --json
chaseos core-export verify-report --json
```

## Rule

Run read-only and dry-run commands before enabling write, export, browser, connector, or publication actions.
