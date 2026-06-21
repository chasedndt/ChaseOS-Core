# ChaseOS Studio Architecture

ChaseOS Studio is the product shell built above ChaseOS Core. It should make the Core operating model visible, navigable, and governable without replacing the underlying file, runtime, and approval contracts.

## Role in the Stack

```text
ChaseOS Core
  -> canonical framework docs, templates, governance, runtime contracts
Private ChaseOS Instance
  -> local notes, projects, runtime state, approvals, and evidence
ChaseOS Studio
  -> app surface over the framework and private instance, with bounded actions
Installer / Release Asset
  -> packaged Studio app distributed separately from normal source history
```

## Core-Owned Contracts Studio Should Render

- Home / operating-system dashboard from `00_HOME` templates.
- Project and knowledge navigation from framework folder roles.
- Agent and runtime status from `06_AGENTS` contracts.
- Approval, review, and promotion-gate queues from governance templates.
- Logs, runs, and evidence views from `07_LOGS` templates.
- Workflow packs and templates from `docs/workflows` and `templates/workflows`.

## Studio Design Principles

1. **Core is the contract; Studio is the interface.** UI code may present and orchestrate, but should not silently redefine folder semantics, approval rules, or promotion authority.
2. **Read first, write through gates.** Any state-changing Studio action should route through the relevant Core approval or runtime contract.
3. **Local-first by default.** Studio can be packaged as an app, but the operator's private instance remains the local source of working truth.
4. **Release artifacts are not source truth.** Large binaries such as `.exe` installers belong in release channels, not the normal public Core commit history.
5. **Public Core stays personal-data free.** Studio docs in Core describe reusable surfaces and contracts, not private instance contents.

## V1 Public Shape

For V1, the public Core repository should include this Studio contract pack plus source-safe app documentation. The packaged `.exe` can be linked from the website and GitHub Releases once privacy, license, and signing checks are complete.
