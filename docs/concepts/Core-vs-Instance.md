# Core vs Private Instance

ChaseOS Core is the **framework**. Everything that makes a deployment *yours* lives
somewhere else. Knowing which side of that line you are on is the first thing to get right
when you fork or adopt Core.

## ChaseOS Core — this repository

The reusable public framework: contracts, schemas, governance rules, templates, and
fail-closed adapters. Core is deliberately inert on its own — it defines what *may* happen
and refuses what it cannot verify.

It contains no credentials, no personal content, and no live runtime state, and it does not
acquire them by being used. That is enforced by the publication standard in
[`CORE_MANIFEST.md`](../../CORE_MANIFEST.md), not merely encouraged.

## Private instance — yours, never here

An instance is a deployment built on Core: real notes and projects, credentials and
provider configuration, live runtime queues, schedules, logs, and local policy.

An instance is also where **authority implementations** live. Core defines the Gate and
approval ports; a deployment registers what actually enforces them. A Core-only install
therefore denies gated operations rather than permitting them — see
[Authority and Trust](Authority-and-Trust.md).

## The line, in practice

| | Core (public) | Instance (private) |
|---|---|---|
| Contracts and schemas | Yes | Consumes them |
| Governance rules | Yes | Applies them |
| Credentials and provider config | Never | Yes |
| Live runtime state, queues, logs | Never | Yes |
| Personal or client content | Never | Yes |
| Authority enforcement | Port only, denies by default | Registered implementation |

## Product layers built above Core

A deployment may put an application on top — an operator UI, a dashboard, a review queue.
Core's position is that such a layer **renders** Core's contracts rather than replacing
them: it may display state, help draft an approval, and route actions to explicit runtime
commands, but it does not become the canonical truth engine and it cannot grant itself
authority the framework withholds.

Documentation for any specific product built this way belongs with that product, not in
Core. Core documents the contracts an application would consume, and stops there.

## Why the split is enforced rather than trusted

A framework that *usually* keeps private state out is a framework that leaks eventually.
Core ships a repo-safe secret audit that reports credential-shaped strings without emitting
their values, and the publication standard is checked before anything ships. The split
survives because it is mechanical.

## Related

- [Core Operating Model](Core-Operating-Model.md) — the seven layers a vault organises
- [FORKING.md](../../FORKING.md) — keeping the split when you fork
- [CORE_MANIFEST.md](../../CORE_MANIFEST.md) — the publication standard
