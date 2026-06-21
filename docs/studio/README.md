# Studio Layer

The Studio layer is the product surface built on top of ChaseOS Core. Core owns the framework contracts: folder roles, governance, runtime boundaries, templates, and evidence requirements. Studio renders those contracts as a desktop/web application without becoming the canonical truth engine.

This folder describes the reusable Studio-facing contract that belongs in public Core. It intentionally excludes private screenshots, local packaged binaries, user-specific runtime logs, machine paths, credentials, and release artifacts.

## Public Core Includes

- Studio architecture and app-boundary docs.
- Runtime integration contracts for read-only status, approvals, activity, and evidence panels.
- Feature surface maps that explain which ChaseOS Core primitives the app should expose.
- Installer/release policy for distributing the Studio application beside the public Core repo.
- Templates for proposing new Studio panels or feature surfaces.

## Public Core Excludes

- Personal vault content and operator history.
- Local packaged `.exe` files in normal Git history.
- Private runtime state, token/config files, queues, and machine-local launchers.
- Screenshots or build logs that reveal personal instance details.
