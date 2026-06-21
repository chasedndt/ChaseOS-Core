# Core Export Pipeline

The Core export pipeline creates a public-safe repo candidate from a private/source ChaseOS workspace.

## Stages

1. Allowlist candidates in `core_export/export_manifest.yaml`.
2. Render templates or sanitized previews.
3. Scan rendered previews for private or unsafe residue.
4. Write dry-run reports under `core_export/reports/`.
5. Verify preview hashes, counts, and scanner status.
6. Manually review public repo shape.
7. Only after approval, create/update a local export target.
8. Only after export verification, approve Git commit/push.

## Non-Negotiable Boundary

Dry-run and report generation must not create the public export target, initialize Git, push, publish, or mutate release assets.
