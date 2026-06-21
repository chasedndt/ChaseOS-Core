# CLI JSON Output Contract

Machine-readable CLI commands should return stable envelopes.

## Envelope

```json
{
  "ok": true,
  "status": "pass",
  "command": "example",
  "data": {},
  "warnings": [],
  "errors": [],
  "safety": {
    "writes_performed": false,
    "credentials_exposed": false
  }
}
```

## Rules

- `ok` must be boolean.
- Write-capable commands must disclose whether writes occurred.
- Dry-run commands must report `writes_performed: false`.
- Errors should be structured, not only printed as prose.
- Secret values must never appear in JSON output.
