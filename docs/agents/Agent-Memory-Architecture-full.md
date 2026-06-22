# Agent-Memory-Architecture.md
## ChaseOS — Formal Multi-Layer Memory Architecture

> This document defines the formal memory architecture for ChaseOS. It specifies how different types of memory are separated, stored, scoped, and used. This is not a description of chat-session memory — it is an architecture for accumulated operating context that grows with the user and the system over time.

**Version:** 1.0
**Created:** 2026-03-23
**Status:** Active — canonical architecture document

---

## Why a Formal Memory Architecture

Most AI tools have one memory model: whatever is in the context window. ChaseOS operates differently. Memory in ChaseOS is:

- **Durable** — it survives across sessions, not just within one conversation
- **Layered** — different types of memory have different scopes, owners, and update rules
- **Inspectable** — memory is stored as structured files, not opaque model state
- **Governed** — what enters memory, how it is updated, and who can access it follows explicit rules
- **Compounding** — the system becomes more useful over time as memory gets richer, sharper, and more connected

The goal is accumulated operating context — a system that gets better as the user keeps learning, building, operating, and writing back into it.

---

## The Five Memory Layers

### Layer A — Shared System Doctrine

**What it is:**
Global rules and constraints the entire ChaseOS operating system follows, regardless of which user, runtime, or task is active.

**What it contains:**
- Gate rules (what can and cannot be written without human approval)
- Knowledge taxonomy (six classes, trust tiers, frontmatter schema)
- Protected file list and modification rules
- Trust tier definitions (authority ceilings for all surfaces and agents)
- Writeback requirements (what must be written, where, and when)
- Agent behavior contracts (what agents can and cannot do)
- Prompt injection defense posture
- Cross-domain routing rules

**Canonical home:**
- `06_AGENTS/Permission-Matrix.md` — permission source
- `06_AGENTS/Trust-Tiers.md` — authority ceilings
- `06_AGENTS/Knowledge-Taxonomy.md` — classification rules
- `06_AGENTS/ChaseOS-Gate.md` — enforcement layer
- `docs/framework-home/Assistant-Contract.md` — agent behavior contract
- `runtime/policy/` — machine-readable policy files

**Durability:** Permanent. Version-controlled. Changes require explicit passes.
**Scope:** System-wide. Applies to all users, runtimes, workspaces, and tasks.
**Update rule:** Protected. Requires deliberate architecture pass with user approval.

---

### Layer B — User-Specific Operating Memory

**What it is:**
What ChaseOS has learned about this specific user — their goals, priorities, working style, recurring structures, personal doctrines, and operating cadences. This layer personalizes system behavior without changing system-wide rules.

**What it contains:**
- User domain profile (active domains, priorities, areas of expertise)
- Preferred working style (response style, writeback expectations, review cadence)
- Personal operating doctrines (principles that govern decisions)
- Recurring workflow patterns (morning thesis, weekly review, ingestion cadence)
- Domain-specific priorities and current sprint focus
- Project continuity context (what each active project is trying to accomplish)
- Personal operating history (patterns the user has confirmed, corrections given)

**Why this layer matters:**
A system that knows the user's domain, priorities, and preferences can tailor its suggestions, context loading, and reasoning without being re-briefed each session. The user does not need to explain what example-project is, what their trading style is, or what writeback means in every session.

**Canonical home:**
- `~/.claude/memory/user_chase_profile.md` — current user profile (Claude lane)
- `docs/framework-home/Now.md` — sprint-level priorities (read every session)
- `docs/framework-home/Principles.md` — personal operating doctrine
- `docs/framework-home/Operating-System.md` — full 18-domain OS definition
- `01_PROJECTS/[Domain]/[Domain]-OS.md` — domain-specific operating state
- Future: `runtime/memory/user/` — structured user memory store for all adapters

**Durability:** Persistent but evolving. Updated at session boundaries and when significant preferences or priorities change.
**Scope:** User-specific. Does not apply across different ChaseOS instances.
**Update rule:** Claude Code writes memory updates directly. User can correct or remove entries. All updates must pass stale-memory review protocol.

**Critical rule:** User-specific memory must not override system doctrine (Layer A). It shapes how doctrine is applied, not whether it applies.

---

### Layer C — Agent/Runtime-Specific Memory

**What it is:**
Per-adapter or per-runtime behavioral profiles. Each runtime that operates inside ChaseOS accumulates its own memory about how it tends to behave, where it performs well, where it makes mistakes, what workflows it has executed, and how its behavior should be guided.

**Why this layer matters:**
Different runtimes (Claude Code, OpenAI Agent Harness, local runtimes, future OpenClaw-style operators) behave differently. A system that tracks these behavioral patterns can apply runtime-specific guidance, corrective context, and execution history — without conflating different adapters' behavior into one undifferentiated memory pool. Without Layer C, the system re-learns the same behavioral facts about a runtime every session.

**What belongs in Layer C:**
- How this runtime tends to behave in ChaseOS (patterns, tendencies, characteristic approaches)
- Task types this runtime performs well at and ones it consistently struggles with
- Known failure modes: what it gets wrong, under what conditions, with what frequency
- Correction history: what was corrected, what the correction was, whether it held
- Workflow execution history: which workflows this runtime has run and their outcomes
- Doctrine adherence record: does it consistently respect routing, writeback, and Gate rules?
- Behavioral drift signals: where outputs or behavior are diverging from expected patterns
- Confidence signals: which types of tasks consistently produce high-quality outputs

**What does NOT belong in Layer C:**
- User preferences, goals, or priorities → those belong in Layer B
- System-wide rules and constraints → those belong in Layer A
- Specific knowledge about a domain → that belongs in `02_KNOWLEDGE/`
- Build session records → those belong in Layer E
- Workspace-local context → that belongs in Layer D

**How Layer C differs from Layer B:**
Layer B is about the user. Layer C is about the runtime. If Chase prefers concise responses, that is Layer B. If the Claude runtime tends to load too much context when not given explicit narrow routing guidance, that is Layer C. These are different observations about different entities.

**How Layer C influences future execution:**
- Before a session begins, the active runtime's Layer C profile informs how the session is structured — what guardrails to apply, what context to pre-load, what failure modes to guard against
- When a runtime has a known tendency to over-expand scope, Layer C memory can trigger a narrowing instruction before the problem occurs
- When a runtime has a strong performance history on a specific workflow type, Layer C memory can inform that this runtime is the right choice for that workflow

**How repeated failures and successes update Layer C:**
- A single failure does not update Layer C; it is logged in Layer E
- A pattern of the same failure across three or more sessions is a candidate for Layer C documentation
- A correction that holds across multiple subsequent sessions is a confirmed Layer C entry
- A successful workflow type with consistent quality metrics is a positive Layer C signal
- The Agent Identity Ledger (see Section 3) is the long-form expression of Layer C for a specific runtime

**Runtime Navigation Map (planned — within Layer C):**
Each runtime accumulates a navigational overlay — a per-runtime record of which vault routes it prefers, which zones it trusts, which paths have led to failures, and where it should escalate rather than act autonomously. This is the **navigational/topological dimension** of Layer C, complementing the behavioral profile (Layer C core) and the Agent Identity Ledger (identity dimension) and Execution Repair Memory (repair dimension).

The Runtime Navigation Map is explicitly not the shared Vault Map. The Vault Map is a static, system-wide structural reference. The Runtime Navigation Map is an evolving, runtime-specific overlay built from operational history. It does not override governance rules — it makes the runtime more efficient within its already-defined permission scope.

Full architecture: `06_AGENTS/Runtime-Navigation-Map.md`

**Current runtime profiles in ChaseOS:**
- **Claude / Anthropic lane** — Tier 2, Anthropic Agent Harness; primary vault-writing surface; profile accumulating in `~/.claude/memory/`
- **OpenAI Agent Harness / Codex lane** — Tier 2 ceiling where configured; profile foothold can be added under `runtime/memory/adapters/`
- **Local/Open-Source Harness** — Tier 2 ceiling, future; no active profile yet
- **OpenClaw / Custom Operator** — active bounded execution lane; structured profile exists at `runtime/memory/adapters/openclaw/profile.json`
- **Hermes** — active coordination/runtime lane; structured profile exists at `runtime/memory/adapters/hermes/profile.json`

**Canonical home:**
- `~/.claude/memory/` — Claude runtime-specific memory (current, partial)
- `runtime/memory/adapters/[adapter-name]/profile.json` — structured per-adapter profile (Phase 9 foothold live)
- `runtime/memory/repair/[adapter-name].json` — structured execution repair memory (Phase 9 foothold live)
- `runtime/memory/scorecards/[adapter-name].json` — runtime execution scorecards
- `runtime/memory/nav/[adapter-name]/nav-map.json` — runtime navigation overlays
- `runtime/memory/adapters/[adapter-name]/identity-ledger.json` — Agent Identity Ledger behavioral record (first Claude/Anthropic formal file live)
- Future: `06_AGENTS/[Adapter]-Runtime-Profile.md` — human-readable profile per adapter
- Future: **Agent Identity Ledger** — see Section 3 below

**Durability:** Persistent. Accumulated across multiple sessions over time.
**Scope:** Runtime-specific. Claude lane memory does not apply to OpenAI lane behavior.
**Update rule:** Updated by harness agents at session boundaries when behavioral patterns are confirmed. Critical corrections written immediately. Stale entries removed when behavior or context changes materially. Single incidents go to Layer E (logs), not Layer C, until a pattern is confirmed.

---

### Layer D — Workspace / Task-Local Memory

**What it is:**
Context specific to one workspace, research question, active run, or execution thread. This memory is temporary and structured — it is relevant only to the current task or workspace, and does not automatically become global memory.

**Why this layer matters:**
Not every piece of context from a research session or SIC workspace query should become permanent memory. Workspace-local memory allows the system to reason with rich, current context during a task without polluting long-term memory with every intermediate artifact.

**What it contains:**
- Workspace object state (sources loaded, topic, context note, output history)
- Active source packages assigned to a workspace
- Task manifest for the current run (objectives, inputs, permitted actions, writeback targets)
- Intermediate reasoning traces that are relevant to the current query
- Retrieved evidence passages for the current query
- Working hypotheses and draft outputs not yet promoted

**Canonical home:**
- `runtime/source_intelligence/workspaces/[workspace-name]/workspace.json` — active workspace state
- `runtime/source_intelligence/indexes/` — index state for the current workspace
- `runtime/tasks/active/[task-id].json` — per-task execution context (Phase 9 foothold live)
- `runtime/tasks/archive/` — archived task-local contexts

**Durability:** Session-scoped or workspace-scoped. Does not persist beyond the active task unless explicitly promoted.
**Scope:** Task/workspace-local. Invisible to other tasks and runtimes unless explicitly shared.
**Update rule:** Created and updated by the active runtime during task execution. Cleared or archived at task completion. May be promoted to Layer B or E if the workspace produces durable insights.

**Promotion boundary:**
Workspace-local memory that should become durable must be explicitly promoted through the ChaseOS Gate. Intermediate reasoning does not automatically become canonical knowledge. See Section 5 (Workspace-Local vs Durable Outputs) in `PROJECT_FOUNDATION.md`.

### Phase 11 Conversation Session History Boundary

Phase 11 Chat may use conversation/session history as durable operating context for long-running `/goal` agents only when that history remains an inspectable Layer D/E artifact, not hidden model memory.

Allowed Phase 11 session-history posture:
- Conversation records live under a declared log destination such as `docs/framework-logs/Conversations/` and are referenced by source hashes, target paths, retention class, and privacy scope.
- Recovery for long histories is bounded rehydration from user-visible summaries and audit-linked chunks, not automatic replay of opaque provider thread state.
- Restored context is task-local operating context for the active goal; it does not become Layer B user memory, Layer C runtime memory, or canonical knowledge without a separate Gate path.
- Every future write of conversation history must also create or reference Layer E audit evidence in `docs/framework-logs/Agent-Activity/`.

Forbidden Phase 11 session-history posture:
- Hidden provider memory, unlisted caches, uninspectable embeddings, or implicit cross-session state.
- Raw credentials, tokens, secret-bearing excerpts, or protected-file content persisted in conversation history.
- Silent promotion from chat history into `02_KNOWLEDGE/`, project truth, runtime profile memory, or user operating memory.
- Automatic long-history injection that bypasses the current approval envelope, retention rules, or privacy scope.

---

### Layer E — Execution-History / Audit Memory

**What it is:**
The accumulated history of workflows run, actions taken, decisions made, failures encountered, and corrections applied. This layer is append-only and permanent — it is the system's memory of what happened.

**Why this layer matters:**
A system that does not learn from its own history repeats the same mistakes. Execution history enables the system to:
- Identify recurring workflow patterns and make them more efficient
- Recognize failure modes and apply learned corrective measures
- Trace the provenance of past outputs back to the context that produced them
- Surface useful patterns for the Agent Identity Ledger (Section 3)

**What it contains:**
- Build logs for every engineering and documentation session
- Agent activity logs for autonomous operations
- Daily and weekly review records
- Decision logs and their outcomes
- Promotion history (what was promoted, from where, when)
- Failure records and how they were resolved
- Recurring workflow records (what patterns repeat)

**Canonical home:**
- `docs/framework-logs/Build-Logs/` — engineering and documentation session logs
- `docs/framework-logs/Agent-Activity/` — agent-initiated action logs
- `docs/framework-logs/Daily/` — daily notes
- `docs/framework-logs/Trading-Weekly/` — weekly reviews
- `99_ARCHIVE/Documentation-History/` — major pass snapshots
- Future: `runtime/audit/` — structured execution audit trail for operator workflows

**Durability:** Permanent. Append-only. Nothing in this layer is deleted — entries may be archived but not removed.
**Scope:** System-wide history, but organized by session, project, or domain.
**Update rule:** Created automatically at session boundaries by Claude Code. Not hand-edited unless correcting errors. Indexed for navigation.

---

## Execution Repair Memory

**Status:** Partial implementation — Phase 9 structured foothold now exists under `runtime/memory/repair/`; automatic promotion and repair application are not built.

Execution Repair Memory is the formal concept for how ChaseOS accumulates knowledge from runtime failures and recoveries. It is a distinct layer within the broader memory architecture — not just a log of what went wrong, but a structured record of what was broken, how it was fixed, and whether the fix proved durable.

**Why this is its own concept:**
Most systems log failures but do not accumulate the repair knowledge. ChaseOS is designed so that a fix that works once becomes a candidate for a pattern, and a fix that works repeatedly becomes part of the operating memory of the runtime. Without this layer, the system re-discovers the same fixes in every session.

**What Execution Repair Memory captures:**

*Repeated failure patterns*
- What type of task or workflow triggered the failure
- The conditions under which it occurs (context size, source type, workspace state, etc.)
- How often this pattern appears across sessions
- Whether the failure is runtime-specific or affects all adapters

*Successful workarounds*
- The specific fix that resolved the failure
- Whether it was a one-time workaround or a durable solution
- How many times the fix has been applied successfully before being promoted

*Browser and operator task repair patterns*
- Specific to web-automation and operator contexts: how the runtime recovered from UI failures, rate limits, timeouts, or unexpected DOM states
- Step-by-step recovery sequences that were proven to work
- Conditions that make recovery more or less reliable

*Workflow correction records*
- A workflow that produced wrong or poor output and how it was corrected
- Changes to prompting, routing, context loading, or writeback that fixed the output quality
- Whether the correction was validated in subsequent runs

*Failure origin classification*
- Was the failure caused by: bad input (quarantine next time), runtime behavior (Layer C update), system architecture (potential Layer A update), external service instability (retry logic), or user error (Layer B note)?

**The repair lifecycle — how a fix moves through the system:**

```
Tier 1 — Runtime-local incident
  First occurrence of a failure + its fix.
  Logged in: docs/framework-logs/Agent-Activity/ or runtime execution log.
  No Layer C or E update yet.

Tier 2 — Recurring pattern candidate
  Same failure and fix pattern appears 2–3 times.
  Action: Flag in Layer C as a known failure mode.
  Document: what triggers it, what fixes it.

Tier 3 — Confirmed operator lesson
  Fix has been applied 3+ times and held reliably.
  Action: Document in 06_AGENTS/[Runtime]-Runtime-Profile.md (future).
  Effect: Pre-applied as guidance before the known failure condition occurs.

Tier 4 — Doctrine candidate
  A repair pattern that reveals a systemic issue in the architecture or governance rules.
  Action: Propose update to Layer A (system doctrine) or Layer B (user operating memory).
  Gate: Requires explicit user decision before promoting to doctrine.
```

**Where Execution Repair Memory lives:**
- **Layer E (Execution History)** — every incident and repair is logged here first
- **Layer C (Runtime-Specific Memory)** — confirmed patterns that affect this runtime's behavior
- **`runtime/memory/repair/`** — structured repair pattern store for machine-readable access
- **Future `06_AGENTS/[Runtime]-Runtime-Profile.md`** — human-readable operator lessons per adapter

**Relationship to the Agent Identity Ledger:**
The Agent Identity Ledger (see next section) tracks the overall behavioral evolution of a runtime. Execution Repair Memory is a specific input to the ledger — it is the failure/recovery dimension of the runtime's behavioral record. A runtime with a rich Execution Repair Memory is one that has been operated extensively enough to accumulate and apply its own lessons.

**Relationship to Autonomous Operator Runtime:**
When the AOR executes long-running operator workflows, Execution Repair Memory informs how it handles failures. Instead of halting on every novel error, a runtime with a proven repair pattern can attempt the known fix before escalating. The AOR manifest can specify: apply known repair patterns before halting, log the outcome, escalate if repair fails.

**Canonical name:** Execution Repair Memory

---

## The Agent Identity Ledger

**Status:** First formal-file foothold live — `runtime/memory/adapters/claude/identity-ledger.json` and `06_AGENTS/Claude-Identity-Ledger.md` are seeded; automated drift scoring and UI surfaces are not built.

The Agent Identity Ledger is a formal architectural feature for tracking the behavior, evolution, and discipline of runtimes that operate inside ChaseOS over time.

It is not a "personality" file. It is a behavioral record — structured, inspectable, and usable by the system.

**What it tracks per runtime:**
- Behavioral tendencies (how this runtime characteristically approaches tasks)
- Performance profile (what it does well, where it struggles)
- Failure and correction log (what went wrong, how it was fixed, whether the fix stuck)
- Workflow execution history (what workflows this runtime has run, with outcomes)
- Doctrine adherence record (does it respect routing, writeback, and Gate rules?)
- Memory cluster influence (what memory inputs shaped recent outputs)
- Behavioral drift signals (is this runtime diverging from expected behavior?)
- Corrections applied over time and their effects

**Where it should live:**
- Per-runtime ledger doc: `06_AGENTS/[Adapter]-Identity-Ledger.md` — human-readable
- Structured data: `runtime/memory/adapters/[adapter-name]/identity-ledger.json` — machine-readable
- Future UI: Agent identity inspector showing behavioral evolution over time

**Current seeded ledger:**
- Human-readable: `06_AGENTS/Claude-Identity-Ledger.md`
- Machine-readable: `runtime/memory/adapters/claude/identity-ledger.json`
- Schema: `runtime/memory/adapters/_identity_ledger_schema.json`

**Why this matters:**
A runtime that is inspectable is also improvable. The ledger creates accountability. It surfaces which runtimes are reliable, which are drifting, and which have accumulated enough corrective context to be trusted with more autonomy.

---

## Memory Layer Summary

| Layer | Name | Durability | Scope | Canonical Home |
|-------|------|-----------|-------|----------------|
| A | Shared System Doctrine | Permanent, version-controlled | System-wide | `06_AGENTS/`, `runtime/policy/` |
| B | User-Specific Operating Memory | Persistent, evolving | User-specific | `~/.claude/memory/`, `docs/framework-home/` |
| C | Agent/Runtime-Specific Memory | Persistent, accumulated | Runtime-specific | `~/.claude/memory/`, `runtime/memory/adapters/`, `runtime/memory/repair/`, `runtime/memory/scorecards/`, `runtime/memory/nav/` |
| D | Workspace/Task-Local Memory | Session or workspace-scoped | Task-local | `runtime/source_intelligence/workspaces/`, `runtime/tasks/` |
| E | Execution-History/Audit Memory | Permanent, append-only | System-wide history | `docs/framework-logs/`, `99_ARCHIVE/` |

---

## How Memory Layers Interact

**Layers do not override each other arbitrarily.** The interaction rules:

1. **Layer A is sovereign.** No other layer can override system doctrine. User preferences (Layer B) shape how doctrine is applied; they do not exempt behavior from doctrine.

2. **Layer B informs reasoning.** When a runtime loads context for a task, Layer B memory helps it understand what this user cares about, what terminology they use, and what their current priorities are — without requiring re-briefing.

3. **Layer C guides runtime-specific behavior.** If the Claude runtime has a known failure mode (e.g., loading too much context), that behavioral profile informs how the Claude lane should be guided. This does not apply to the OpenAI lane.

4. **Layer D is ephemeral by default.** Workspace-local context does not propagate to other layers automatically. A deliberate promotion step is required.

5. **Layer E feeds back into all layers.** Execution history informs the Agent Identity Ledger (Layer C refinement), surfaces patterns for user memory updates (Layer B), and may trigger corrections to system doctrine (Layer A) via explicit architecture passes.

---

## How Memory Grows With the User

ChaseOS memory is designed to compound over time. The system becomes more useful as:

| What gets richer | Effect |
|-----------------|--------|
| Source archive depth | More source material available for SIC retrieval |
| Doctrine sharpness | Better, more consistent decisions and routing |
| Project state continuity | Agents can re-orient faster with less re-briefing |
| User-specific memory | System applies user preferences without prompting |
| Runtime-specific memory | Runtimes avoid known failure modes |
| Cross-domain linking | Association across domains becomes possible |
| Recurring workflow patterns | Repeated workflows become more efficient |
| Execution-history knowledge | System learns from past runs |
| Recovered failure-handling patterns | The system gets better at its own failure modes |
| Prior output provenance | Every generated output is traceable to its inputs |

This is not "chat memory." This is accumulated operating context. The architecture ensures that useful context is not lost between sessions, not conflated across layers, and not allowed to override governance rules.

---

## What Is Not Memory

**Do not put in memory:**
- Active project state → goes in `01_PROJECTS/[Project]-OS.md`
- Build session outputs → go in `docs/framework-logs/Build-Logs/`
- Promoted knowledge → goes in `02_KNOWLEDGE/`
- Canonical decisions → go in decision logs or project OS files
- Git history → `git log` is authoritative
- Current file contents → read the file; do not cache it as memory

Memory is for **what the system should know across sessions that is not directly derivable from reading current files.**

---

## Future Directions

**Near-term / current Phase 9 foothold:**
- Per-runtime memory files formalized in `runtime/memory/adapters/`
- Execution repair memory formalized in `runtime/memory/repair/`
- Workspace-local memory formalized in `runtime/tasks/` structure
- `chaseos memory ...` provides a read-only Layer C/D inspector, including `memory summary` for consolidated validation, runtime-family coverage, attention items, and advisory governance boundaries
- Agent Identity Ledger structure defined per active runtime

**Phase 9+ (Operator Runtime):**
- Automated memory updates from operator workflow outcomes
- Runtime behavioral monitoring feeding the Agent Identity Ledger
- Memory-informed routing for autonomous workflows
- Runtime Navigation Map: first population for Claude/Anthropic lane from Layer E history; curation protocol established; AOR reads nav-map before autonomous workflow route selection (see `06_AGENTS/Runtime-Navigation-Map.md`)

**Phase 10 (Interface Layer):**
- User-facing memory inspector — what does the system know about me?
- Runtime behavior dashboard — how are my runtimes performing?
- Provenance graph — where did this output come from?
- Navigation map inspector — how does each runtime move through the vault? (hot/cold node visualization, route audit trail, escalation point surface)

---

*Graph links: [[Vault-Map]] · [[SIC-Architecture]] · [[Knowledge-Taxonomy]] · [[ChaseOS-Gate]] · [[Permission-Matrix]] · [[Trust-Tiers]] · [[Claude-Memory-System]] · [[Autonomous-Operator-Runtime]] · [[Feature-Register]] · [[Runtime-Navigation-Map]]*

*Agent-Memory-Architecture.md - v1.5 | Created: 2026-03-23 | Updated: 2026-04-28 (Layer C/D foothold live; Agent Identity Ledgers seeded; consolidated read-only memory summary surface added)*
