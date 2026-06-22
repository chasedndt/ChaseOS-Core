# Studio Brand Asset Generation Plan

Status: `DOCS-ONLY / GOVERNED PLAN / NO ASSETS GENERATED`

Date: 2026-05-22
Runtime: Codex
Session descriptor: `studio-brand-candidate-directions`

## Purpose

This plan defines how ChaseOS should move from brand direction to reviewable assets without accidentally treating exploratory concepts as final product truth.

It exists because the repo now has canonical brand foundation docs, but the final logo, wordmark, Studio UI treatment, installer branding, and asset pack remain planned.

## Current Answer

The operator does not need to provide a finished logo before work can continue.

Codex can prepare candidate directions, prompts, concept-generation instructions, asset inventories, and validation gates from the existing brand docs. The operator must approve the chosen direction before any final asset generation, UI mutation, installer packaging, signing, release promotion, or canonical brand completion claim.

## Source Docs

- [ChaseOS_Brand_Foundation.md](ChaseOS_Brand_Foundation.md)
- [ChaseOS_Logo_Visual_Identity_Brief.md](ChaseOS_Logo_Visual_Identity_Brief.md)
- [ChaseOS_Logo_Candidate_Directions.md](ChaseOS_Logo_Candidate_Directions.md)
- [Design_Tokens_Preliminary.md](Design_Tokens_Preliminary.md)
- [Brand_Copy_Bank.md](Brand_Copy_Bank.md)

## Stage 0: Candidate Direction Documentation

Status: `COMPLETE IN THIS PASS`

Outputs:

- Candidate logo directions.
- Recommended primary exploration path.
- Prompt seeds.
- Selection checklist.
- Operator approval choices.

Blocked:

- final logo claim
- source SVG creation
- icon export
- UI redesign
- installer packaging
- legal/trademark claim

## Stage 1: Operator Direction Selection

Status: `NEXT OPERATOR DECISION`

Required operator decision:

- approve one primary direction, or
- approve a limited two-direction exploration, or
- request direction revisions.

Suggested accepted statement format:

```text
I approve ChaseOS brand exploration using [direction name] as the primary direction. This approval is for concept exploration only, not final logo adoption, UI redesign, installer packaging, signing, release promotion, or legal/trademark clearance.
```

Recommended options:

- `Sovereign Core`
- `Command Kernel`
- `Quiet Wordmark + System Glyph`
- `Sovereign Core + Command Kernel comparison`

## Stage 2: Concept Generation Preview

Status: `PLANNED`

Goal:
Produce review-only concept images or sketches.

Recommended output folder:

```text
docs/brand/concepts/YYYY-MM-DD_studio-logo-concept-generation-preview/
```

Recommended outputs:

- 8 to 12 Sovereign Core concept previews, if selected.
- 4 to 6 Command Kernel concept previews, if selected.
- 3 ChaseOS wordmark studies.
- 1 black-and-white test sheet.
- 1 small-size app icon test sheet.
- 1 concept review matrix.

Rules:

- Mark every output as `CANDIDATE / NOT FINAL`.
- Preserve prompts and negative prompts.
- Do not overwrite source-of-truth brand docs.
- Do not create runtime final asset paths yet.
- Do not mutate Studio UI.

## Stage 3: Selected Concept Refinement

Status: `PLANNED`

Required before this stage:

- operator-selected concept ID
- explicit confirmation that refinement is still not final legal adoption

Recommended outputs:

- refined symbol study
- refined wordmark study
- dark/light contrast study
- icon-size test
- monotone test
- similarity-risk notes

## Stage 4: Source Asset Pass

Status: `PLANNED`

Required before this stage:

- operator-selected refined direction
- approval to create source assets in runtime brand paths

Target paths from current Studio brand contract:

```text
runtime/studio/brand/source/chaseos-studio-logo.svg
runtime/studio/brand/source/chaseos-studio-logo.png
runtime/studio/brand/source/chaseos-studio-symbol.svg
```

Rules:

- SVG master should be editable.
- Source bitmap should be generated from the same approved direction.
- Include dark and light background tests.
- Keep final-source files separate from concept previews.
- Do not package installer assets yet.

## Stage 5: Icon And Export Pack

Status: `PLANNED`

Required before this stage:

- approved SVG source
- approved symbol-only source

Target paths:

```text
runtime/studio/brand/icons/chaseos-studio.ico
runtime/studio/brand/icons/png/
```

Recommended icon sizes:

- 16 px
- 24 px
- 32 px
- 48 px
- 64 px
- 128 px
- 256 px
- 512 px
- 1024 px

Validation:

- small-size legibility
- dark/light background visibility
- square app icon framing
- Windows icon bundle inspection
- no accidental background artifacts

## Stage 6: UI Token And Preview Pass

Status: `PLANNED`

Required before this stage:

- approved palette or source logo colors
- operator approval for preview-only UI work

Target path:

```text
runtime/studio/brand/tokens/studio-brand-tokens.json
```

Allowed work:

- create token JSON
- create isolated preview screens
- update documentation screenshots only after preview verification

Blocked without later approval:

- live Studio redesign
- broad CSS rewrite
- behavior change
- runtime authority change

## Stage 7: Installer Brand Asset Packaging Preview

Status: `PLANNED`

Required before this stage:

- approved icon/export pack
- selected installer technology or existing packaging target
- explicit packaging preview approval

Target paths:

```text
runtime/studio/brand/installer/
runtime/studio/brand/installer/shortcut-preview.png
```

Blocked unless separately approved:

- signing
- startup/autostart
- shortcut creation on host
- registry writes
- release promotion
- Git push or release publication

## Required Approval Gates

| Gate | Required Before |
|---|---|
| Direction approval | Concept generation preview |
| Concept selection | Refinement |
| Source asset approval | SVG/source asset creation |
| Icon/export approval | ICO and PNG export pack |
| UI preview approval | Token/UI preview work |
| Installer preview approval | Installer/shortcut asset previews |
| Release approval | signing, packaging, release promotion, host mutation |

## Completion Criteria

The ChaseOS Studio brand asset pack is not complete until:

- source SVG exists
- source PNG exists
- symbol-only SVG exists
- Windows ICO exists
- PNG icon set exists
- token JSON exists
- dark/light previews are verified
- small-size icon tests are documented
- similarity/originality review is recorded
- font-license notes are recorded
- packaging references are updated only where proven
- build log, history note, daily note, and agent activity records are indexed

## Warnings

- The brand foundation describes the product vision more broadly than the current implementation proves.
- Current repo truth does not prove final UI redesign, final logo, branded installer, legal originality, or trademark clearance.
- Current Studio proof is not release-grade branded installer completion.
- Future image generation may create useful concepts, but generated images are not automatically ownable final marks.
- Final vector work and originality review are required before public use.

## Next Recommended Pass

`studio-brand-operator-direction-selection`

After operator direction approval, continue to `studio-logo-concept-generation-preview`.
