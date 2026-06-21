# Studio App Boundary

This boundary defines what the Studio app may do when it is built above ChaseOS Core.

## Studio May

- Display Core folder roles, docs, templates, runtime status, and evidence paths.
- Help an operator draft approvals, reviews, feature specs, workflow packs, and handoff packets.
- Route actions to explicit CLI/runtime commands with visible inputs and outputs.
- Package a local operator UI as a desktop app or website download.
- Show readiness, blockers, and next manual steps for gated workflows.

## Studio Must Not

- Promote knowledge into canonical areas without the configured Gate.
- Treat UI state as the source of canonical truth when Core files/contracts disagree.
- Bundle personal vault contents, credentials, runtime logs, or machine-local paths into public releases.
- Commit large packaged binaries to normal Git history unless a deliberate LFS/release policy says so.
- Hide runtime actions behind ambiguous buttons that bypass operator review.

## Public Repository Boundary

Public Core should include Studio contracts, source-safe templates, setup docs, and app-source candidates after review. Private instance logs, screenshots, local packaged builds, and release binaries should remain excluded or published only as explicit release assets.
