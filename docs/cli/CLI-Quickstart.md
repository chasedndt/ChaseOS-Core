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
chaseos capture image-text-status --json
chaseos capture image-text ./explicit-local-screenshot.png --title "Screenshot text" --vault-root . --json
```

`capture image-text` is optional Core functionality: it reads only an explicit local PNG, runs the repo-owned local image text engine, and writes extracted text through the normal quarantine capture path. It does not enable cloud OCR, provider calls, ambient screen capture, browser-profile access, SIC ingestion, or canonical promotion.

## Rule

Run read-only and dry-run commands before enabling write, export, browser, connector, or publication actions.
