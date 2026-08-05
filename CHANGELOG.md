# Changelog

All notable changes to ChaseOS Core are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project has not yet adopted
Semantic Versioning tags beyond `0.1.0` (see [`ROADMAP.md`](ROADMAP.md) for what "Core
complete" means).

## [Unreleased]

### Added
- `runtime/connections` — local-first provider manifest registry (Discord, Slack, Telegram,
  WhatsApp Business Cloud, WhatsApp personal lab, iMessage Mac, GitHub, local files), with a
  SQLite-backed local registry and read-only-by-default, approval-gated write/egress.
- `runtime/decision_router` — schema-validated Decision Modality Router
  (`chaseos decision-route inspect`) that selects human/rules/ML/genAI modality and derives
  an approval plan without dispatching the route.
- `runtime/policy/gateway_allowlists.json` — gateway allowlist policy for connection tooling.
- `runtime/capture/visual_capture` — optional local image-to-text capture path.
- `docs/ARCHITECTURE.md` — public architecture map with layer, module, and flow diagrams.
- `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, this changelog, and `.github/` issue/PR templates
  and CI.

- `examples/` — three runnable scripts (decision-route inspection, a custom
  `GateProvider` registration, connection manifest loading) that execute in CI.
- `ruff.toml` plus a CI lint gate scoped to defect-class rules, and a wheel-install CI job
  that verifies packaged data files are present and usable outside the source tree.
- `docs/RELEASING.md` and `.github/workflows/publish.yml` — PyPI publishing via Trusted
  Publishing, with the built wheel verified before upload.
- `docs/concepts/` — an ordered entry path, a glossary, and concepts for authority/trust,
  decision modality routing and multi-runtime coordination, plus a runtime topology diagram.
- `runtime/memory/intelligence.py` — a deterministic, lexical memory-candidate harness
  (scoring, near-duplicate detection, categorisation) with an evaluation fixture and tests.
  Local-only by design: no provider calls, embeddings, memory mutation, canonical promotion
  or network access.

### Fixed
- **Packaging:** non-editable installs shipped no data files. `pip install chaseos-core`
  outside a source checkout produced an empty provider list (reported as `ok`) and a
  `CatalogError` from `chaseos commerce catalog`. All 14 YAML/JSON data files are now
  declared as package data.
- `chaseos doctor` reported a healthy vault for any directory containing a `.chaseos`
  folder. It now names which marker matched and gives remediation when none does.
- `07_LOGS/Agent-Activity/` is now git-ignored; without it a fork committed its own AOR
  run records on first `chaseos run`.

### Changed
- Expanded `runtime/cli/core_main.py` CLI surface for connections and decision routing.
- README now states that `chaseos run` resolves to `escalated` in Core, because Core ships
  no workflow manifests. It previously implied working workflow execution.
- Relocated the internal Connections bootstrap handover note to
  `docs/runtime/Connections-Bootstrap-Notes.md` and de-referenced the local fork path
  placeholder it previously contained.

## [0.1.0] — published to PyPI 2026-08-05

First release on the Python Package Index: `pip install chaseos-core`. Published via
Trusted Publishing from `.github/workflows/publish.yml` after a full TestPyPI rehearsal.
Verified from a clean environment against the live index — CLI runs, all eight provider
manifests load, the commerce catalogue resolves, the decision router works as a library,
and the gate denies by default.

### Original contents — 2026-06-25

### Added
- Initial public MIT release: local-first runtime substrate (AOR, schedules, capture,
  memory, graph, gate interface), lean Core CLI (`chaseos`), governance docs, templates,
  and SOPs curated from the private ChaseOS monorepo.
