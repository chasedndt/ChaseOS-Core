# FORKING.md
## How to Fork ChaseOS

> ChaseOS is designed to be forkable. This document explains what the framework is meant to let people replace, what should remain standardized, and how to populate your own instance correctly.

**Last updated:** 2026-03-19

---

## What Forking Means in ChaseOS

Forking ChaseOS means taking the framework conventions — the folder structure, note types, routing rules, agent contracts, and operating discipline — and populating them with your own context.

You are not copying someone else's personal operating system. You are using the structural scaffold to build your own.

The framework tells you:
- How to organize your memory
- How to structure your project governance
- How to route context to agents and tools
- How to bound agent authority
- How to maintain writeback discipline
- How to think about your own identity layer

The framework does not tell you:
- What your projects are
- What your principles are
- What domains you operate in
- What your goals are
- Who you are

Those are yours to define.

---

## What to Keep Standard

These files and conventions should remain stable across forks. They are the framework, not the personal layer.

**Folder structure:**
```
docs/framework-home/
01_PROJECTS/
02_KNOWLEDGE/
03_INPUTS/
04_SOPS/
05_TEMPLATES/
06_AGENTS/
docs/framework-logs/
99_ARCHIVE/
```
The numeric prefix system and the role each folder plays should not be changed without good reason. Changing these breaks the routing logic that agents rely on.

**File naming conventions:**
- Project OS files: `[ProjectName]-OS.md`
- Knowledge index files: `[Domain]-[Type].md`
- Build logs: `YYYY-MM-DD-[Project]-[descriptor].md`
- Daily notes: `YYYY-MM-DD.md`
- Weekly reviews: `YYYY-Wxx-Weekly-Review.md`
- Input files: `YYYY-MM-DD-[topic-slug].md`

**Template structure:**
Copy and populate the templates in `05_TEMPLATES/`. Do not diverge from their structure unless you are intentionally extending the framework — and document the extension.

**Agent routing conventions:**
The Vault Map pattern (`06_AGENTS/Vault-Map.md`) should be maintained in every fork. Agents need to know where to look. If you change the folder structure, update the Vault Map.

**Agent permission model:**
The trust/permission tiers (read / write / modify core / delete) from `docs/framework-home/Assistant-Contract.md` should be preserved. You may adjust specific rules but should not remove the permission model entirely.

**SOPs:**
Keep the Build-Log-SOP and Research-Ingest-SOP or equivalent. The writeback and ingestion discipline is framework-level — it is not personal preference.

**Agent output conventions:**
`06_AGENTS/Agent-Output-Conventions.md` defines how any agent backend (not just Claude Code) should write output into the vault. Keep this file and update it when you add new agent backends to your stack.

**Root documentation files:**
`README.md`, `PROJECT_FOUNDATION.md`, `ROADMAP.md`, and `FORKING.md` should be rewritten for your own instance but should continue to exist and serve the same purpose.

---

## What to Replace

These files must be replaced with your own content. Do not use the original owner's personal context.

**Identity layer:**
- `SOUL.md` — use `SOUL.template.md` as the starting point, populate with your own identity
- `docs/framework-home/Principles.md` — your doctrine, your decision rules, not someone else's

**Operating system:**
- `docs/framework-home/Operating-System.md` — your domains, your projects, your tiers
- `docs/framework-home/Dashboard.md` — your status, your links, your system state
- `docs/framework-home/Now.md` — your current sprint priorities
- `docs/framework-home/Tech-Pillars.md` — your core technical disciplines (if applicable)

**Project layer:**
- All files in `01_PROJECTS/` — replace with your own project OS files
- Use the Project-OS-Template for each new project

**Knowledge layer:**
- All files in `02_KNOWLEDGE/` — replace with your own domain knowledge index files
- Rename domain folders to match your own domains if they differ

**Agent configuration:**
- `06_AGENTS/Agent-Registry.md` — your agents, your trust levels
- `06_AGENTS/Tool-Map.md` — your tools, your accounts

---

## How to Populate Your Own Context

### Step 1 — Define your operating system
Before populating any files, define your domains. What areas of life or work does your operating system cover? How many are there? What are their relationships?

Write this out in `docs/framework-home/Operating-System.md` using the same format: a named domain list with a letter or number identifier, a short description, and the key projects or initiatives within each.

Do not try to copy the original's 18-domain structure if it does not fit your life. Start with what is real and extend later.

### Step 2 — Populate your identity files
Fill in:
- `SOUL.md` from the `SOUL.template.md` starting point
- `docs/framework-home/Principles.md` with your actual decision rules and operating doctrine
- `docs/framework-home/Dashboard.md` with your real domain links and status

Do this before touching any project or knowledge files. The identity layer is the context that makes everything else coherent.

### Step 3 — Create project OS files for your active projects
For each active project or domain, create a `[ProjectName]-OS.md` using the `05_TEMPLATES/Project-OS-Template.md`.

Fill in: mission, key components, current status, 30/60/90 day goals, open loops, and key resources.

Do not create empty OS files as placeholders. Only create them for projects that are real and active.

### Step 4 — Set your current sprint focus
Populate `docs/framework-home/Now.md` with what you are actually focused on right now.

This is the file agents read first. If it is empty or wrong, agents will be miscalibrated from the start.

### Step 5 — Configure your agents
Update `06_AGENTS/Agent-Registry.md` with the AI tools and assistants you actually use.
Update `06_AGENTS/Tool-Map.md` with your actual stack.

Do not include references to tools you do not have access to or accounts you do not own.

### Step 6 — Begin using the SOPs
Start using `04_SOPS/Build-Log-SOP.md` for every engineering or build session.
Start using `04_SOPS/Research-Ingest-SOP.md` when you process raw input into knowledge.

The SOPs are only valuable if they are used consistently from the start.

---

## How to Think About Your Domains

ChaseOS uses a domain model. Each domain is a major area of operation with its own project OS, knowledge base folder, and potentially its own SOP.

When defining your domains, consider:

- What are the major areas of your life or work that require ongoing attention?
- Which of those areas have active projects right now?
- Which require a knowledge base (accumulated understanding over time)?
- Which have enough complexity to warrant an OS file?

Not everything needs to be a domain. Not every domain needs to be equally deep. A domain with one active project and a small knowledge base is fine.

Avoid creating domains for things that are genuinely trivial or transient. The domain model is a commitment — it implies ongoing maintenance.

---

## How Private Context Differs from Framework Boilerplate

**Framework boilerplate** is what you fork. It includes: folder structure, templates, SOPs, routing conventions, permission model, agent contract pattern, and SOUL.template.md.

**Private context** is what you create. It includes: your identity, your principles, your projects, your domain knowledge, your agents, your logs, and your history.

The boundary is this: if someone else could meaningfully use the file as-is without knowing anything about you, it is framework boilerplate. If it only makes sense because of who you are and what you are doing, it is private context.

Never commit or share private context in a public fork. This includes:
- Personal identity files (Principles, SOUL, Operating-System)
- Project OS files with real project details
- Build logs with personal work history
- Agent configs with real API references or account details
- Knowledge notes with private research

---

## Warnings Around Credentials and Private Data

ChaseOS files are plain markdown. They are easily committed to version control and potentially shared.

**Never store in the vault:**
- API keys or tokens
- Passwords or secrets
- Private keys (crypto or otherwise)
- Account credentials for any service
- Personal financial information that should not be public
- Private keys or seed phrases of any kind

If you need to reference a tool that requires credentials, reference the credential store location (e.g., "see 1Password vault entry X"), not the credential itself.

**Before making a fork public:**
- Audit every file in `docs/framework-home/`, `01_PROJECTS/`, `02_KNOWLEDGE/`, and `06_AGENTS/` for personal information
- Check build logs and daily notes for accidentally included private details
- Check tool map and agent registry for API references or account-specific information
- Strip or generalize all personal context before publishing

**Git history is permanent.** If you accidentally commit a credential or sensitive file, removing it from the current version does not remove it from history. Treat this as a serious risk and audit carefully before any public commit.

---

## Extending the Framework

If you build on the framework in ways that seem generally useful — new templates, new SOP patterns, new agent contract formats, improved routing logic — consider documenting the extension clearly and contributing it back to the upstream framework.

Extensions that are personal (your specific domain knowledge, your specific project patterns) belong in your private instance. Extensions that are structural (better templates, improved SOPs, cleaner routing) can be framework-level.

When extending, document in `PROJECT_FOUNDATION.md` what you changed from the upstream framework and why.

---

*Graph links: [[06_AGENTS/Agent-Output-Conventions|Agent-Output-Conventions]] · [[SOUL.template]]*

*FORKING.md — framework guidance for ChaseOS instances.*
*Version: 0.1 | Created: 2026-03-19*
