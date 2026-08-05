## Summary

What does this PR change, and why?

## Scope check

- [ ] This change belongs in Core (public framework), not a private instance — see
      [`CORE_MANIFEST.md`](../CORE_MANIFEST.md).
- [ ] No credentials, live runtime state, personal vault content, or machine-local paths
      are included.
- [ ] Ran the repo-safe secret audit (see [`CONTRIBUTING.md`](../CONTRIBUTING.md)) before
      opening this PR.

## Test plan

- [ ] `pytest` passes locally
- [ ] `chaseos doctor --vault-root . --json` passes
- [ ] Added/updated tests for the behavior change (or explain why not needed)

## Related issues

Closes #
