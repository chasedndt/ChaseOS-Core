# Installation

This document describes a public-safe installation path for ChaseOS Core.

## Requirements

- Git
- Python 3.11+ or the runtime version declared by the active release
- A local editor
- Optional: a private Obsidian vault or markdown workspace

## Install pattern

```bash
git clone <your-chaseos-core-repo-url> chaseos-core
cd chaseos-core
```

If runtime code is included in your release, install dependencies using the release-specific command documented in `docs/cli/CLI-Quickstart.md`. If runtime code is not included, treat this package as documentation/templates only.

## Safety setup

Before adding personal files:

- choose a private Personal workspace path;
- review `.gitignore` policy;
- keep credentials outside the repo;
- keep raw captures and logs outside public Core;
- verify the repository remote points to the intended GitHub repo.

## Success check

You have a clean Core checkout and a separate private workspace for personal content.
