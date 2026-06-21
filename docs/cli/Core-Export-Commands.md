# Core Export Commands

Core export commands generate sanitized public candidates from a private/mixed source workspace.

## Safe sequence

```bash
chaseos core-export build --dry-run --write-report --json
chaseos core-export verify-report --json
chaseos core-export next-step --json
```

Only after scanner-clean previews and manual review should a guarded local export be considered.

## Non-goals

Core export commands must not silently initialize Git, commit, push, publish, promote canonical knowledge, or copy private folders by default.
