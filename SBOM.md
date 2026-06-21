# SBOM — ChaseOS Core

Software Bill of Materials for `chaseos-core` v0.1.0. ChaseOS Core is **stdlib-first**:
it has **zero required runtime dependencies**. This is intentional — the substrate
relies only on the Python standard library (`sqlite3`, `json`, `urllib`, `pathlib`,
`hashlib`, etc.).

## Runtime dependencies

| Component | Version | License | Notes |
|---|---|---|---|
| _(none)_ | — | — | Python standard library only |

- **Python:** `>=3.11`

## Development / test dependencies (optional extra: `.[dev]`)

| Component | Version | License |
|---|---|---|
| pytest | >=9.0.0 | MIT |
| pytest-cov | >=7.0.0 | MIT |
| PyYAML | >=6.0.0 | MIT |

## Bundled / integrated (not pip dependencies)

- **Hermes**, **OpenClaw** — independent upstream MIT runtimes integrated via
  governed adapters (not vendored here). See `THIRD_PARTY_NOTICES.md`.

## Notes

- This human-readable SBOM covers the Core package. A machine-readable
  `SBOM.spdx.json` should be generated as part of the public release pipeline
  (e.g. `syft` / `pip-audit`) and committed before tagging a public release.
- ChaseOS Studio and ChaseOS Cloud are separate proprietary products and are **not**
  part of this SBOM.
