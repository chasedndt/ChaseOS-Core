---
type: sop
system: ChaseOS
title: Feature Filter SOP
version: "1.0"
created: 2026-03-31
status: active
---

# Feature Filter SOP

**Version:** 1.0
**Status:** Active
**Created:** 2026-03-31

---

## Purpose

The Feature Filter is a 6-question formal gate that every feature proposal must pass before it can enter the ChaseOS roadmap as an adopted feature.

It exists to prevent three failure modes:
1. **Roadmap drift** — features added because they're interesting, not because they fit the system architecture
2. **Phase scope inflation** — features added to a phase that already has sufficient work
3. **Implementation-before-design** — features built before their layer, dependency, and permission model is understood

If a proposal cannot answer all 6 questions, it is not ready to adopt. It is not rejected — it is parked in `06_AGENTS/Feature-Fit-Register.md` with status "Proposed" until it can answer them.

---

## When to Use This SOP

Run a Feature Filter pass when:
- A new capability idea arises during a build session
- An external capability (tool, API, system) is proposed for integration
- A workflow that was "Phase X+" becomes ripe for implementation
- A second-wave feature is being evaluated for promotion to first-wave

Do NOT run this SOP for:
- Bug fixes (not a feature)
- Doc corrections (not a feature)
- Refactoring existing features (not a new feature)
- Truth-sync passes (not a feature)

---

## The 6 Filter Questions

Answer all 6 before marking a feature "Adopted".

---

### Q1: What problem does it solve in ChaseOS specifically?

Not "what is this feature" — what gap in ChaseOS does it close?

**Acceptable answers:** Closes a specific operator workflow gap. Removes a manual step in a defined domain. Enables a Phase capability that is currently blocked.

**Not acceptable:** "It would be nice to have." "Other systems have this." "It might be useful someday."

---

### Q2: Which ChaseOS layer does it belong to?

ChaseOS has defined layers. Every feature must map to exactly one layer:

| Layer | What lives here |
|-------|----------------|
| Capture | Connectors, quarantine ingestion, dedup |
| Source Intelligence Core | Source packages, workspaces, retrieval, output generation |
| Operator Runtime (AOR) | Scheduled workflows, operator automation, execution pipelines |
| Gate / Governance | Permission enforcement, trust tiers, protected-file guards |
| Interface/Experience | CLI, GUI, inspector tools, operator dashboard |
| Vault / Knowledge | Knowledge notes, templates, SOPs, project OS files |

If the feature doesn't map cleanly to a layer, it is not yet well-defined enough to adopt.

---

### Q3: What does it depend on that doesn't exist yet?

List every runtime module, workflow, schema, or capability that this feature requires but is not yet built.

If the dependency list is long, the feature may belong in a later phase.

---

### Q4: What is the permission ceiling?

Every feature has a maximum permission level it should ever require. Answer:

- Does it need to read protected files? (If yes: why? Is there an alternative?)
- Does it need to write beyond its declared write scope?
- Does it involve external network calls? (If yes: what data leaves the vault?)
- Does it involve Tier 4 (untrusted) inputs? (If yes: how is prompt injection prevented?)

If the permission ceiling is not well-understood, the feature is not ready to adopt.

---

### Q5: What are the failure modes?

How can this feature go wrong? For each failure mode:
- What does the user see?
- Does it corrupt vault state?
- Is it recoverable?

Features without a defined failure model should default to escalate-on-failure, never silently continue.

---

### Q6: What is the Phase and pass sequence?

- Which phase does it belong to? (match to ROADMAP.md)
- Which pass within that phase?
- What are the success criteria (Definition of Done)?
- What tests confirm it works?

If you cannot name a pass, a DoD, and a test, the feature is not ready to be engineered.

---

## Outcome States

| Outcome | Meaning | Next Step |
|---------|---------|-----------|
| **Adopted — First Wave** | All 6 questions answered; in the active phase's first wave | Engineering begins in current phase |
| **Adopted — Second Wave** | All 6 questions answered; depends on first-wave completion | Engineering begins after first-wave done |
| **Proposed** | Interesting but cannot yet answer all 6 questions | Park in Feature-Fit-Register.md; revisit next planning pass |
| **Rejected** | Does not fit any ChaseOS layer; solves no real gap | Remove from consideration; log in Feature-Fit-Register.md with rationale |
| **Later Candidate** | Correct layer exists but violates governance constraints at current scale | Park with explicit governance requirements; do not implement until constraints met |

---

## Template

Use `05_TEMPLATES/Feature-Filter-Template.md` to structure a Feature Filter pass.

---

## Relationship to Other Docs

- `06_AGENTS/Feature-Fit-Register.md` — canonical triage table; outcome goes here
- `06_AGENTS/Phase9-Adopted-Feature-Specification.md` — adopted features get full specs here
- `ROADMAP.md` — adopted features get roadmap entries here
- `docs/framework-logs/Decision-Ledger/` — if the filter outcome is a significant decision, record it there

---

*Feature Filter SOP — Phase 9 Pass 1. Version 1.0.*


*Graph links: [[06_AGENTS/Vault-Map|Vault-Map]]*
