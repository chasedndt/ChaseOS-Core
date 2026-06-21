# Third-Party Notices — ChaseOS Core

ChaseOS Core (MIT, (c) 2026 ChaseOS Ltd.) integrates with third-party components,
each under its own licence. An upstream licence does not automatically cover every
bundled asset or dependency — these are attributed separately.

## Independent upstream runtimes (out-of-process, not vendored)

| Component | Relationship | Licence |
|---|---|---|
| Hermes Agent | governed adapter / out-of-process runtime | third-party MIT (upstream) |
| OpenClaw | governed adapter / out-of-process runtime | third-party MIT (upstream) |

These are independent upstream projects integrated via governed adapter glue
(original ChaseOS code); their source is not vendored here. Confirm the exact
upstream repository, version, and SPDX identifier before redistribution or managed
hosting.

## Python dependencies

Runtime and development dependencies are declared in `pyproject.toml`; each retains
its own licence. Generate a full `SBOM.spdx.json` before any public release — this
file is a human-readable summary, not a substitute for the SBOM.
