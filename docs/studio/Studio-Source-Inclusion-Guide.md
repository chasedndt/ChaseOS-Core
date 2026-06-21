# Studio Source Inclusion Guide

Studio source can belong in ChaseOS Core when it is reusable, source-safe, and contract-aligned.

## Include Candidates

- App shell source that renders Core contracts.
- Panels for read-only status, approvals, evidence, runtime health, and feature specs.
- Service-layer code that talks to explicit CLI/runtime contracts.
- Tests that prove no hidden canonical mutation.
- Build metadata needed to reproduce the app.

## Exclude Candidates

- Packaged `.exe` files in normal Git history.
- Private screenshots, local smoke logs, and proof captures.
- Local runtime queues, credentials, absolute machine paths, or host-specific launchers.
- Experimental one-off scripts that only make sense for a private operator instance.

## Release Path

Source-safe Studio code can be committed to Core. The installer should be published as a website/GitHub Release asset with checksums and release notes.
