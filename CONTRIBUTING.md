# Contributing to ChaseOS Core

Thanks for considering a contribution. Core is the public framework layer of ChaseOS — a
local-first, approval-gated scaffold for governed human-AI systems. Contributions should
strengthen that framework, not smuggle in private/proprietary surfaces.

## Before you start

- Read [`CORE_MANIFEST.md`](CORE_MANIFEST.md) and [`PROJECT_FOUNDATION.md`](PROJECT_FOUNDATION.md)
  for what belongs in Core versus a private instance.
- Read [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the layer model and module map.
- For anything nontrivial, open an issue first to align on scope before writing code.

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -e .[dev]
chaseos doctor --vault-root . --json
pytest
```

## Making changes

- Keep changes scoped to what the issue/PR describes — avoid bundling unrelated refactors.
- Match existing patterns: approval-gated writes, fail-closed adapters, read-only-by-default
  provider manifests (see [`kernel/PERMISSION_MATRIX.md`](kernel/PERMISSION_MATRIX.md)).
- Add or update tests under `tests/` for any behavior change.
- Run the linter before pushing — CI blocks on it:

  ```bash
  ruff check .
  ```

  The gate covers defect-class rules only (undefined names, mutable default
  arguments, redefinitions), not style. [`ruff.toml`](ruff.toml) documents why each rule
  is selected and which exemptions are deliberate.
- Do not commit credentials, live runtime state, personal vault content, or machine-local
  paths. Before opening a PR, run the repo-safe secret audit:

  ```bash
  python - <<'PY'
  from runtime.repo_secret_audit import audit_repo_secrets, format_repo_secret_audit
  print(format_repo_secret_audit(audit_repo_secrets('.')))
  PY
  ```

## Commit messages and PRs

- Write commit messages that explain *why*, not just *what*.
- One logical change per PR where practical.
- Fill out the PR template — it asks for a test plan, not just a description.

## Reporting bugs / requesting features

Use the issue templates under `.github/ISSUE_TEMPLATE/`. For security issues, do **not**
open a public issue — see [`SECURITY.md`](SECURITY.md).

## Code of conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md).
