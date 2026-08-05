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

### Changed
- Expanded `runtime/cli/core_main.py` CLI surface for connections and decision routing.
- Relocated the internal Connections bootstrap handover note to
  `docs/runtime/Connections-Bootstrap-Notes.md` and de-referenced the local fork path
  placeholder it previously contained.

## [0.1.0] — 2026-06-25

### Added
- Initial public MIT release: local-first runtime substrate (AOR, schedules, capture,
  memory, graph, gate interface), lean Core CLI (`chaseos`), governance docs, templates,
  and SOPs curated from the private ChaseOS monorepo.
