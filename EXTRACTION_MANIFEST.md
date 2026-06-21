# ChaseOS Core — Extraction Manifest

> How this MIT repository is curated out of the private ChaseOS monorepo, what is
> included today, what is deferred pending dependency-untangling, and the ordered
> dependency-break plan. Operational record for the Core/Cloud split (see the
> monorepo's `docs/commercial-readiness/Repository-Split-and-Public-Export-Plan.md`
> and ADR-0002).

## Method

- Source of truth: the private ChaseOS monorepo. Built on an **orphan branch**
  (`core-mit-curated`) — clean history, none of the private commit history.
- **Allowlist-based** inclusion (only verified-clean paths), not denylist.
- Every included module is checked for **top-level (col-0, non-test) imports of
  excluded modules** (must be none), **imported live** from the worktree root, and
  the whole tree is **secret-scanned (0 findings required)** before commit.
- A module is extractable only if it has NO top-level hard import of an EXCLUDED
  module (studio, commerce, cli, mcp, workflows, operator_surface, aor, …). Lazy /
  try-except imports degrade to no-ops and do not block.

## Included (verified import-clean; imports live from worktree root; 0 secrets)

| Path | Tier | Why it is clean |
|---|---|---|
| `runtime/agent_bus/` | core-mit | Coordination/task bus + pluggable SQLite backend; only Studio touch is optional/lazy/fail-open. |
| `runtime/schedules/` | core-mit | Native Schedule Intent layer; zero cross-module imports. |
| `runtime/net/` | core-mit | SSRF egress guard — stdlib only, zero runtime imports. |
| `runtime/security/` | core-mit | injection_scan + prompt_guard + redaction — stdlib only. |
| `runtime/platform_support/` | core-mit | OS/platform abstraction — clean. |
| `runtime/context/` | core-mit | Boot-context protocol — clean (optional yaml). |
| `runtime/lifecycle/` | core-mit | Local supervisor/descriptors; only in-core (agent_bus) + a function-local installer import that no-ops when absent. |
| `runtime/execution_adapters/model_config.py` | core-mit | Model-config seam — stdlib + dataclasses, zero runtime imports (the seam `providers` depends back on). `execute.py` deferred. |
| `runtime/common/` | core-mit | Dependency-free shared utilities. `simple_yaml.py` = stdlib YAML-subset parser (hosts the parser the n8n adapters used to import from `runtime.aor.registry`). |
| `runtime/adapters/` (codex / n8n / openai) | core-mit | Execution adapters. Break #2 **done**: the two n8n files now import `_parse_simple_yaml` from `runtime.common.simple_yaml` instead of `runtime.aor.registry` (28 n8n tests pass in the monorepo). `codex/test_codex_daemon.py` excluded (test-only studio/cli coupling). |
| Public repo files | n/a | LICENSE (MIT), README, NOTICE, SECURITY, CONTRIBUTING, CODE_OF_CONDUCT, GOVERNANCE, TRADEMARKS, THIRD_PARTY_NOTICES, pyproject, .gitignore. |

Two synthetic-secret-fixture tests were **excluded** so the Core secret-scan is clean
(`security/test_redaction.py` — fake API keys used to test the redactor;
`lifecycle/test_hermes_gateway_config.py` — sentinel tokens). The source they cover
ships; re-add them later with a scanner allowlist.

## Excluded — never in MIT Core

- **Proprietary:** `runtime/studio/`, `runtime/commerce/`, Control-Kernel enforcement.
- **Personal-instance:** `00_HOME/`, `01_PROJECTS/`, `02_KNOWLEDGE/`, `03_INPUTS/`,
  `05_PROMPTS/`, `07_LOGS/`, `SOUL.md`, `.obsidian/`, `.chaseos/`, `.hermes/`, venvs.
- **Private history** (orphan branch).

## Deferred — Core-eligible, needs a dependency-break first (ordered least-coupled-first)

| # | Module | Break required |
|---|---|---|
| 1 | `runtime/providers` | Delazify `routing_consumer_contract.py:22` `from runtime.studio.provider_readiness import …` → function-local (pattern already at `governance_layer.py:7255`). One line. Then confirm transitive deps (config.store, lifecycle.health_cli, chaseos_gate) are Core/stubbed. |
| 2 | `runtime/adapters` | Hoist `_parse_simple_yaml` out of `runtime.aor.registry` into a shared Core util; repoint `n8n/workflow_policy.py:15` + `n8n/mcp_connection.py:25`. (codex/ + openai/ subtrees already clean.) |
| 3 | `runtime/siteops` | Relocate `runtime/mcp/yaml_compat.py` (~130-line stdlib shim) into Core; repoint `tenancy.py:9`. Exclude `candidate_promotions.py` (imports browser_skills + chaseos_gate; not re-exported). |
| 4 | `runtime/execution_adapters/execute.py` | Gated on (1) providers landing Core-clean + the `governance_layer↔model_config` cycle (model_config now extracted resolves it in the Core direction). |
| 5 | `runtime/events` | Deepest/structural: inject a workflow-runner/manifest-resolver protocol + an injectable Gate policy hook to decouple `dispatcher.py` from `runtime.aor` (×2) and `runtime.chaseos_gate`. |

## Keep proprietary / special

- `runtime/forge/` — **keep proprietary.** Import-clean, but it *is* the excluded
  commerce plane (marketplace, install/approval lifecycle, Studio panels). Any
  Core-eligible primitive needs an explicit per-file carve-out, not a bulk migrate.
- `runtime/policy/` — data only; ship as **sanitized overridable Core templates**
  (today's `protected_files.yaml` + `gateway_allowlists.json` encode this instance's
  paths/config). Deferred pending a sanitization pass.

## Next steps

1. Do break #1 (providers delazify) → migrate providers; then #2–#5 in order.
2. Add a compatibility test suite + `SBOM.spdx.json` before any public release.
3. Operator: adopt `core-mit-curated` as the repo default + publish (gated on
   legal/operator sign-off; public-history rewrite). Nothing is pushed by this extraction.

## Template / instance-scaffold layer (Option A — 2026-06-21)

Added the **framework + instance-scaffold template layer** so a forker gets a usable
ChaseOS instance, not just the runtime package. 110 pre-sanitized doc/template files
applied from `core_export/templates/` per `core_export/export_manifest.yaml`
(`mode: core_template` entries only): `00_HOME/*.example` + folder READMEs,
`05_TEMPLATES/`, `04_SOPS` (credential boundaries), `06_AGENTS/` framework contracts
(Permission-Matrix, Trust-Tiers, Agent-Control-Plane, ChaseOS-Gate, Knowledge-Taxonomy,
SIC-Architecture, …), `docs/` (getting-started, concepts, cli, agents, runtime,
governance, workflows, studio, github), `templates/`, `security/`, `99_ARCHIVE/`.

**Deliberately EXCLUDED (honoring the current commercial architecture):**
- **All proprietary code** — `runtime/studio/`, `runtime/commerce/`, `runtime/forge/`
  (Studio/Cloud/Control-Kernel are PROPRIETARY, not MIT Core).
- The repo's own brand/strategy docs were kept (curated `README`/`LICENSE`/`SECURITY`/
  `NOTICE`); the export manifest's `README.core`/`PROJECT_FOUNDATION.core`/`ROADMAP.core`/
  `CORE_MANIFEST.core` were **skipped** (older "source-available" narrative — needs a
  refresh for the MIT-Core-vs-proprietary-Studio split before shipping).

**KNOWN CONFLICT (flagged to operator):** `core_export/export_manifest.yaml` (built
under the older source-available strategy) lists `runtime/studio/*.py` and broader
runtime code as `mode: copy` "Core" entries. The built `chaseos core-export` pipeline
must **NOT** be run as-is — it would publish proprietary Studio source. This manual,
import-verified extraction supersedes it for the proprietary-Studio architecture.

**v0 note:** docs describe a fuller runtime (CLI/AOR/etc.) than the engine subset
currently shipped (deferred modules need dependency-breaks). Acceptable for a
"preparing for release" Core; close the gap as modules land.

Verified: `audit_repo_secrets` 0 findings; 0 studio/commerce/forge code; 0 real
logs/SOUL/personal markers. Tree: 296 files (186 engine + 110 template/doc).

## Core growth pass (2026-06-21) — Source Intelligence Core extracted; remaining map

**Extracted now (verified):** `runtime/source_intelligence/` (SIC, 50 files) — its only
top-level runtime deps are self + Core; **no proprietary, no other-deferred, no heavy
external imports** (embedding backends lazy/optional). All submodules import clean;
`test_core_publish_readiness.py` extended to assert it (13/13 pass); secret audit 0.

**Extractability map of the remaining deferred modules** (top-level, col-0 deps on
non-Core runtime modules — from a full scan):

| Module | Files | Blocking top-level deps | Risk |
|---|---|---|---|
| `memory` | 13 | `pulse` (card_schema: EvidenceRef/now_utc), `cli` | MED — relocate the 2 `pulse.card_schema` symbols + drop a CLI wrapper |
| `capture` | 28 | `cli`, `browser_runtime`, `acquisition` (visual_capture path) | MED — core capture (content_packet/router/intake_writer/dedup/connectors) is clean; visual_capture + acquisition adapter are the coupled tail |
| `providers` | 7 | `studio.provider_readiness`, `config.store`, `chaseos_gate` | HIGH — needs delazify + Core Gate-interface + config seam |
| `siteops` | 17 | `cli`, `chaseos_gate`, `mcp.yaml_compat`, `browser_skills` | HIGH — relocate yaml_compat→common, drop candidate_promotions, Gate-interface, de-CLI |
| `events` | 2 | `aor`, `chaseos_gate` | HIGH — gated on aor + Gate-interface |
| `mcp` | 35 | `aor`, `chaseos_gate`, `cli` | HIGH |
| `aor` | 15 | `chaseos_gate`, `cli`, `execute`(→providers), `acquisition`, `sbp`, `workflows`, `osril` | HIGH — most coupled |
| `workflows` | 26 | `aor`, `siteops`, `ventureops`, `acquisition`, `operator_surface`, `capture`, `sbp`, `execute`, `hermes`, `cli` | HIGH — top of the dependency tree |

**Key unblocker:** a **Core Gate-interface** (a Core-side protocol that the proprietary
`chaseos_gate` implements, so modules import the interface, not the concrete gate) plus
de-coupling `aor`/`cli` would unblock `providers` → `events` → `siteops` → `aor` → `mcp`.
That is the next focused engineering pass (monorepo refactor + test run per module),
done one module at a time with the import-clean + secret-scan gate — not bulk.

## Core growth pass 2 (2026-06-21) — runtime/memory extracted

**Dependency-break (monorepo):** `runtime/memory`'s only non-Core dep was 2 symbols
(`EvidenceRef`, `now_utc`) from `runtime.pulse.card_schema`. Hoisted them into Core
`runtime/common/evidence.py` (dependency-free); `pulse.card_schema` now re-exports them
(its 68 consumers unaffected); repointed memory's 7 source files + 3 schema tests to
`runtime.common.evidence`. Monorepo verified: **pulse + memory suites 502 passed / 271
subtests, exit 0**.

**Extracted:** `runtime/common/evidence.py` + `runtime/memory/` (52 files) into Core.
**Excluded** (recorded; re-add later): `memory/scorecards/test_scorecard_updater.py`
(one in-function `runtime.aor` integration test — re-add when `aor` lands) and
`memory/test_personal_map_applied_persistence.py` (synthetic openai-key fixture — re-add
with a scanner allowlist).

**Gate:** boundary test 14/14 (memory now in CORE_PACKAGES); Core memory suite 30
passed / 6 subtests; `audit_repo_secrets` 0 findings; 0 studio/commerce/forge.

Remaining order: capture (MED — core capture clean; visual_capture/acquisition/cli tail),
then the HIGH cluster behind the Core Gate-interface.

## Core growth pass 3 (2026-06-21) — core capture extracted

**Extracted** the clean core-capture subset (14 files): `content_packet`, `router`,
`intake_writer`, `dedup_registry`, `capture`, `watch_folders`, all `connectors/`
(cli/rss/browser/perplexity/grok) + `test_pass8.py`. **No monorepo break needed**
(`capture.py`'s `post_capture_hooks` import is already a fail-open lazy import).

**Excluded** (coupled tail): `post_capture_hooks.py` (→aor; degrades fail-open),
`visual_capture/` (entire subpackage → `browser_runtime`), and 18 tests that import
`runtime.cli`/`acquisition` or the removed `visual_capture` (the connector tests drive
the `chaseos capture` CLI). Re-add visual_capture + CLI tests when browser_runtime /
cli land in Core.

**Gate:** boundary test 17/17 (capture in CORE_PACKAGES); Core capture test_pass8.py 25
passed; secret audit 0; no instance data; no tracked bytecode.

**Remaining = HIGH cluster only** (providers, events, siteops, aor, mcp, workflows):
all blocked on a **Core Gate-interface** (Core-side protocol the proprietary
`runtime.chaseos_gate` implements) + `aor`/`cli` decoupling. This is an architectural
decision + a large monorepo refactor across the proprietary plane — **not safe
autonomous extraction.** Recorded as BLOCKED-pending-architecture; the safe-extraction
loop stops here.

## Core growth pass 4 + CRITICAL hygiene scrub (2026-06-21)

**Extracted (clean framework modules):** `schemas` (provenance contracts), `core_export`
(export tooling), `install_safety`, `audit_writeback`, `installer` (clean once
install_safety landed — fixes lifecycle's install path). Boundary suite + module suites
green; secret audit 0.

**CRITICAL: a secret-audit-only gate had let per-instance data into public Core across
earlier passes. Scrubbed from HEAD + gate hardened:**
- Removed `runtime/adapters/codex/runs/` + `CODEX_CLI_LIVE_TEST_HANDOFF.md` (execution
  artifacts), `runtime/source_intelligence/workspaces/{phase7-test,vcmi-reviewed-captures}/`
  (populated workspaces with REAL captured research content), `runtime/agent_bus/agent_bus.sqlite`
  (committed DB with task records + paths), and path-hardcoded tests
  (audit_writeback/test_smart_embed, source_intelligence/indexes/test_pass7,
  lifecycle/test_consumer_vault_root_portability + test_coordination_watch_bootstrap +
  test_startup_surfaces). Genericized a personal path in `runtime/lifecycle/README.md`.
- **Gate strengthened** (`test_core_publish_readiness.py`): added `test_no_personal_paths`
  (scans tracked files incl. binary-as-text for the user's home/vault markers) on top of
  `test_no_instance_memory_data` + `test_no_tracked_bytecode`. `.gitignore` now excludes
  `*.sqlite*`, `runtime/adapters/*/runs/`, `runtime/source_intelligence/workspaces/*/`.

**HISTORY CAVEAT (operator action):** the scrubbed content is removed from HEAD but
remains in earlier commits (engine batches; SIC `feb8a96`). Because real captured
content + paths were public, recommend **re-privatising `ChaseOS-Core` and rewriting/
squashing the orphan branch history to a clean state** before it stays public. HEAD is
clean; history is not.

**Loop paused** pending operator decision on the history rewrite + remaining HIGH
cluster (providers/events/siteops/aor/mcp/workflows — need the Core Gate-interface).
