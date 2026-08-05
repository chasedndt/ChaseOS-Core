# Releasing ChaseOS Core

ChaseOS Core is published to the Python Package Index (PyPI) as
[`chaseos-core`](https://pypi.org/project/chaseos-core/), so it can be installed as an
ordinary dependency:

```bash
pip install chaseos-core
```

## One-time setup

You only do this once for the project. It requires a PyPI account.

### 1. Create accounts

- **PyPI** — https://pypi.org/account/register/ (the real index)
- **TestPyPI** — https://test.pypi.org/account/register/ (a throwaway index for rehearsals;
  separate account, same process)

Enable two-factor authentication on both. PyPI requires 2FA for maintainers of any project,
and you will be locked out of publishing without it.

> One PyPI account covers every package you ever publish. Package **names** are claimed
> individually and permanently by whoever registers them first, but you do not need a new
> account per project.

### 2. Configure Trusted Publishing (no API tokens)

The release workflow uses **Trusted Publishing** (OIDC), where PyPI verifies the GitHub
Actions run directly. This is the current recommended approach and means **no API token is
ever stored in GitHub secrets** — nothing to leak, nothing to rotate.

On PyPI, go to *Your projects → Publishing* (or *Account settings → Publishing* for a
project that does not exist yet) and add a pending publisher:

| Field | Value |
|---|---|
| PyPI project name | `chaseos-core` |
| Owner | `chasedndt` |
| Repository name | `ChaseOS-Core` |
| Workflow name | `publish.yml` |
| Environment name | `pypi` |

Repeat on TestPyPI if you want to rehearse there first.

### 3. Create the GitHub environment

In the repository: *Settings → Environments → New environment*, named `pypi`. Adding a
required reviewer here means every publish needs a human approval click — recommended,
since releases are effectively permanent.

> **API tokens instead of Trusted Publishing.** If you prefer tokens, create one at
> *PyPI → Account settings → API tokens*, store it as the `PYPI_API_TOKEN` repository
> secret, and add `password: ${{ secrets.PYPI_API_TOKEN }}` to the publish step. Trusted
> Publishing is preferred because it avoids holding a long-lived credential. Never paste a
> token into a file, an issue, or a commit — it grants publish rights to your package.

## Cutting a release

1. Update `version` in [`pyproject.toml`](../pyproject.toml).
2. Move the `[Unreleased]` items in [`CHANGELOG.md`](../CHANGELOG.md) under the new version.
3. Commit and tag:

   ```bash
   git tag -a v0.1.1 -m "v0.1.1"
   ```

4. Push the tag, then publish a GitHub Release for it. Publishing the Release triggers
   `.github/workflows/publish.yml`.

The workflow builds an sdist and wheel, **installs the wheel outside the source tree and
verifies it actually works** (provider manifests load, catalogue resolves), runs
`twine check`, and only then uploads.

That verification step exists because of a real failure: an early build shipped 160 Python
files and zero data files, so a non-editable install returned an empty provider list
without erroring. Releases are immutable, so this is checked *before* upload, not after.

## Rehearsing

To publish to TestPyPI without cutting a real release, run the workflow manually
(*Actions → publish → Run workflow*) with target `testpypi`, then verify:

```bash
pip install --index-url https://test.pypi.org/simple/ \
            --extra-index-url https://pypi.org/simple/ chaseos-core
```

The extra index is needed because TestPyPI does not mirror real dependencies (PyYAML).

## Rules worth knowing

- **Versions are immutable.** You can *yank* a released version (hiding it from new
  installs) but you can never re-upload the same version number. A mistake means shipping
  a new patch version.
- **Names are permanent.** `chaseos-core` belongs to whoever registers it first. Register
  it even if the first real release is later.
- **Pre-releases** (`0.2.0rc1`) are not installed by default, so they are a safe way to
  publish something for testing without affecting `pip install chaseos-core`.
- Because Core is alpha and pre-1.0, treat any minor bump as potentially breaking and say
  so in the changelog.
