---
title: ChaseOS Setup Surface
status: active
created: 2026-04-25
updated: 2026-04-27
---

# ChaseOS Setup Surface

## Purpose

Canonical CLI truth:
- operator-facing `setup` commands are owned by `runtime.cli.main:main`
- installed `chaseos` / `chase` point there directly
- `chaseos.py` and `runtime/cli.py` are compatibility shims only

The `setup` family is the home for:
- provider onboarding
- integration onboarding
- setup-state visibility
- setup validation
- future menu/wizard-driven configuration

It is deliberately separate from `runtime ...`, which is reserved for lifecycle-backed runtime lanes.

## Current subcommands

Preferred canonical forms:

```powershell
chaseos setup status --json
chaseos setup validate --json
chaseos setup set provider openai configured=true api_key_present=true secret_reference_present=true secret_reference_kind=env-var secret_reference_target=OPENAI_API_KEY default_model=gpt-5 reasoning_policy=balanced --dry-run --json
chaseos setup set provider openai configured=true api_key_present=true secret_reference_present=true secret_reference_kind=env-var secret_reference_target=OPENAI_API_KEY default_model=gpt-5 reasoning_policy=balanced --json
chaseos setup provider list --json
chaseos setup provider show openai --json
chaseos setup provider validate openai --json
chaseos mvp credential-handoff --json
chaseos setup provider wizard claude --json
chaseos setup integration list --json
chaseos setup integration validate telegram --json
chaseos setup discord validate --json
chaseos setup menu
```

Compatibility invocation paths still work through the same parser:
- `python chaseos.py setup ...`
- `python runtime\cli.py setup ...`
- `python -m runtime.cli.main setup ...`

## State surfaces

- `runtime/setup_registry.json`
- `runtime/setup_provider_profiles.json`
- `runtime/setup_state.example.json`
- `runtime/setup_state.json`
- `runtime/setup_state.schema.json`

## Safety model

Current setup development intentionally stores non-secret posture/configuration state only.
It does not yet persist raw provider API keys or raw integration tokens.

Discord control-plane validation is also no-secret. `chaseos setup discord validate --json` reads `config/chaseos-example/control_surface_bindings.example.yaml`, reports binding readiness for Studio and runtime lanes, and returns only presence/status fields. It must not print raw Discord IDs, webhook URLs, bot tokens, public keys, or other secret values.

Credential boundary checks are now enforced at the setup-state writer:
- allowed: env var names such as `OPENAI_API_KEY`
- allowed: keychain-style references such as `keychain://service/account`
- allowed: template placeholders such as `SET_OPENAI_SECRET_REF`
- blocked: raw API keys, tokens, passwords, private keys, webhook secrets, or pasted credential values

`setup set ...` supports `--dry-run` for previewing non-secret metadata updates before writing. Live `setup set ...` and wizard `--apply` paths must write only credential references and presence flags. If a command attempts to write a secret-bearing field such as `api_key=<raw-value>` or `token=<raw-value>`, the command fails before `runtime/setup_state.json` is updated.

## Setup init scaffold boundary

`chaseos setup init` is for scaffold/bootstrap surfaces only.
It should create:
- framework/product files
- orientation placeholders
- governance/setup/runtime substrate files
- index/status/instruction surfaces

The instruction surface should not live only as markdown. `setup init` should also surface onboarding instructions directly in CLI output, and later through the future interface/product shell.

`setup init` should also behave like an onboarding summary surface, showing what was created, what remains planned, and what the operator should do next.

Within the scaffold-only boundary, it should seed not only top-level orientation files but also folder-local canonical index-note surfaces where ChaseOS navigation conventions depend on them.

The scaffold model should also distinguish between:
- canonical folder-local index surfaces
- convenience surfaces
- orientation surfaces
- OS-core surfaces

That distinction matters for future CLI summaries and future interface presentation.

Dry-run summaries should make it obvious when important scaffold families are part of the model, even if they are not being materialized into the live current vault during development.

The CLI should be able to control scaffold families directly. `setup init` should support family-level inclusion/exclusion so a future menu/interface can present the same toggles to the operator during setup.

Convenience surfaces should not duplicate canonical folder-local indexes under alternate root-level names. Canonical navigation should live at the folder-local path unless there is a very strong product reason for an additional surface.

Scaffold note naming should default to normal Obsidian-friendly title-case node names rather than full-uppercase filenames. Prefer forms like `Knowledge-Index.md`, `Setup-Instructions.md`, and `System-Status.md` unless a file is intentionally machine-style.

Do not force title-case renames onto already-established framework/reference docs just for naming consistency. The title-case preference is mainly for note-like scaffold surfaces, not for every legacy or product-root document.

When reviewing scaffold coverage, prefer removing redundant note surfaces over multiplying near-duplicate navigation notes.

In particular, avoid keeping a convenience knowledge index when a canonical folder-local knowledge index already exists, and avoid adding an OS map note when `Vault-Map.md` already covers that role sufficiently.

OS-level scaffold review should also check for missing operator-facing map surfaces, not just runtime profiles and registry/state files.

OS-core note surfaces should include only those operator-facing navigation surfaces that add distinct value beyond existing `Vault-Map.md`, runtime profiles, and orientation notes.

It should not fabricate:
- personal project truth
- knowledge notes
- daily history
- build history
- operator brief history
- secrets or credentials

The rule is simple: scaffold substrate, do not invent lived state.
