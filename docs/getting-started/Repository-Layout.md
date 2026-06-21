# Repository Layout

ChaseOS Core uses a public-friendly layout rather than mirroring a private Obsidian vault.

```text
chaseos-core/
  README.md
  CORE_MANIFEST.md
  docs/
  templates/
  sops/
  runtime/
  legacy-map/
```

## Sections

- `docs/` — architecture, concepts, CLI usage, governance, workflows, and examples.
- `templates/` — reusable neutral starting points.
- `sops/` — procedures that can be adapted safely.
- `runtime/` — schemas and examples first; executable runtime code only when explicitly released.
- `legacy-map/` — mapping from historical/private ChaseOS folder semantics to public Core sections.

## Not included

Personal projects, source notes, logs, private runtime state, credentials, raw inputs, and active agent-bus packets do not belong in the public Core repository.
