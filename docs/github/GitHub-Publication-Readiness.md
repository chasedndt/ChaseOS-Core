# GitHub Publication Readiness

Use this checklist before committing or pushing ChaseOS Core publicly.

## Required Checks

- Core export dry-run verifies scanner-clean.
- Manual preview review passes.
- Real export target verifies clean after approved export.
- `.gitignore`, license, security policy, and README are public-ready.
- No personal notes, logs, credentials, queues, local paths, or packaged binaries are committed.
- Studio installer is handled as a release artifact, not normal source history.
- Remote binding points to the intended public Core repository.

## Commit Gate

Do not commit from the mixed private source vault. Commit only from the generated and verified Core export tree.
