---
type: sop
title: Build Log SOP
updated: 2026-03-19
---

# 🏗️ Build Log SOP — Standard Operating Procedure

> Every build session gets a record. No exceptions.
> If it's not logged, it didn't happen as a system.

---

## Purpose
Capture what was built, decided, and left open in every development or tinkering session — so context is never lost between sessions, and a portfolio record accumulates over time.

---

## When to Use This
Any time you:
- Code something with Claude Code or solo
- Make architecture decisions for a project
- Set up infrastructure or tooling
- Debug or fix something significant
- Design a system or workflow

---

## Operation Mode

**Agent-assisted (default):** When Claude Code is conducting or supporting the session, it should create and populate the build log directly — not prompt the user to do it. If the correct log path or scope is unclear, Claude Code flags the ambiguity and asks before proceeding. Manual prompting of the user is fallback behavior only.

**Solo (fallback):** If working without Claude Code, follow the manual steps below.

Note: fully autonomous scheduled logging is a future Phase 7 system capability. Current behavior is best-effort agent-assisted writeback within the existing vault structure.

---

## Step-by-Step Process

### Before the Session (< 2 min)
1. Create a new entry in `docs/framework-logs/Build-Logs/` using the naming format:
   ```
   YYYY-MM-DD-[Project]-[brief-descriptor].md
   ```
   Example: `2026-03-18-example-tool-scoring-engine-design.md`
   — **If working with Claude Code:** Claude Code creates this file directly at session start or close.
   — **If working solo:** create the file manually.
2. Use the build log template structure below.
3. Write a 1-line **Session Goal** — what is being accomplished?

### During the Session
- Capture quick notes as the session progresses (what worked, what broke, decisions made)
- Don't over-format in the moment — capture first, tidy at close
- Note any external references used (docs, Stack Overflow, etc.)

### After the Session (< 5 min)
**If working with Claude Code:** Claude Code writes the completed log directly — fills in What Was Done, Decisions Made, and Open Loops based on the session, then flags any Project-OS.md updates needed.

**If working solo:**
1. Fill in the **What Was Done** section — bullet points are fine
2. Fill in the **Decisions Made** section — even small decisions matter
3. Fill in the **Open Loops** — anything incomplete or unresolved
4. Update the **Project-OS.md** if the session changed the project status
5. Push code to GitHub if applicable

---

## Build Log Entry Template

```markdown
---
type: build-log
project: [Project Name]
date: YYYY-MM-DD
session-duration: [Xh Xm]
---

## 🎯 Session Goal
[One line — what were you trying to do?]

## ✅ What Was Done
- 
- 

## 🔑 Decisions Made
- 

## 🔗 Open Loops
- [ ] 

## 📎 References & Resources Used
- 

## 📸 Screenshots / Artefacts
[Paste or link any relevant outputs]
```

---

## File Location
All build logs go in: `docs/framework-logs/Build-Logs/`

Organised by date — no subfolders needed unless volume demands it.

---

## Related
- [[05_TEMPLATES/Experiment-Template|Experiment Template]] — for structured experiments
- [[04_SOPS/Research-Ingest-SOP|Research Ingest SOP]] — for research sessions
- [[06_AGENTS/Agent-Registry|Agent Registry]] — for AI tool context


*Graph links: [[06_AGENTS/Vault-Map|Vault-Map]]*
