# ROADMAP — ChaseOS Core

A public, honest roadmap for the MIT Core. Capabilities are marked **live** only with
evidence. Nothing here implies a hosted service, public installer, billing, managed
agents, or a marketplace are live.

## Now — v0 (shipped in this repo)

- **Runtime substrate** (stdlib-first, zero required deps): Agent Bus + pluggable
  storage backend; native Schedule Intent layer; `net` (SSRF guard); `security`
  (injection scan / redaction / prompt guard); `context` (boot protocol); `lifecycle`
  (local descriptors); `platform_support`; `execution_adapters/model_config`; `common`;
  `adapters` (codex / n8n / openai).
- **Framework + instance scaffold**: governance contracts, templates, synthetic
  examples, getting-started, concepts, and CLI / agent / runtime / workflow / governance
  docs.
- **Forker protections**: instance-aware `.gitignore`, Core-vs-Personal separation,
  `SBOM.md` (zero required runtime deps).

## Next — Core growth (engineering)

- **Migrate deferred runtime modules into Core** as their proprietary coupling is
  broken, ordered and import-verified: `providers` → `execute` → `siteops` → `events`;
  then `aor`, `mcp`, `memory`, `workflows`, `source_intelligence`, `capture` as their
  carve-outs land.
- **Machine-readable SBOM** (`SBOM.spdx.json`) + a **compatibility / import-clean test
  suite** wired into CI.
- **Developer surface**: a versioned SDK and published schema packages.

## Boundaries — not live

- No public installer, hosted runtime, managed agents, billing, credits, or marketplace.
- ChaseOS Studio and ChaseOS Cloud are separate **proprietary** products with their own
  release tracks; this roadmap covers the MIT Core only.

## How Core grows

A module enters Core only when it is **import-clean** (no compile-time dependency on the
proprietary layers), **secret-clean**, and reviewed. The method, allowlist, and the
ordered remaining breaks are recorded in `EXTRACTION_MANIFEST.md`.
