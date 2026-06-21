# Installer and Release Policy

ChaseOS Core and the Studio installer can be released together, but they are not the same artifact.

## Recommended V1 Distribution

- **GitHub repository:** source-safe ChaseOS Core docs, templates, contracts, examples, and reviewed app source when ready.
- **GitHub Release / website download:** packaged Studio installer such as `.exe`, checksums, release notes, and signing/notarization notes.
- **Private instance:** personal vault data, local runtime state, logs, credentials, and operator-specific configuration.

## Do Not Commit by Default

- `dist/`, `build/`, installers, large binaries, packaged app bundles.
- `.env`, token files, local database files, runtime queues, private logs.
- Screenshots or proofs that reveal private workspace contents.

## Release Checklist

1. Public Core export verifies scanner-clean.
2. Studio source/docs are reviewed as source-safe.
3. Installer is built from the reviewed source state.
4. Checksums are generated and published with the release.
5. Website download points to the release asset, not to an untracked local file.
6. Release notes distinguish framework updates from app binary updates.
