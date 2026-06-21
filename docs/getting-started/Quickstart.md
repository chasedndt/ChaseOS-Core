# ChaseOS Core Quickstart

ChaseOS Core is a reusable operating-system framework for personal knowledge, projects, governed agent runtimes, and repeatable workflows. This quickstart gets a new fork from empty repository to a safe first local instance.

## First principles

- Start with Core as framework code and documentation.
- Keep Personal data in a separate private instance.
- Do not commit credentials, raw inputs, private logs, runtime memory, or live agent-bus state.
- Promote durable truth through a review gate rather than direct agent writeback.

## Minimal first run

1. Clone or fork `chaseos-core`.
2. Read `CORE_MANIFEST.md` and `docs/getting-started/Core-vs-Personal.md`.
3. Create a private Personal workspace from the templates, not by editing public examples in place.
4. Copy only neutral templates you need.
5. Configure runtime/provider settings outside tracked public docs.
6. Run validation commands from `docs/cli/CLI-Quickstart.md` before enabling automation.

## First safe workflow

Begin with a docs-only workflow: create a synthetic project note, log a decision, and review it through the approval/review pattern. Do not start with browser, shell, credentials, publication, or connector automation.
