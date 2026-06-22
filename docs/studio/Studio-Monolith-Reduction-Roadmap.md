# Studio Monolith Reduction Roadmap

> Purpose: reduce ChaseOS Studio / Agent Control Plane architectural risk without breaking working product surfaces. This roadmap treats `app.js`, `api.py`, and `launcher_update_check.py` as legacy compatibility surfaces and grows tested source-owned modules around them.

## Why this affects more than Agent Control Plane

The risk is broader than the Agent Control Plane screen. The large files are cross-cutting Studio infrastructure:

- `runtime/studio/shell/frontend/app.js` owns or touches Chat, Runtime controls, Settings, Studio panels, terminal/workbench flows, route state, visual status, and many product interactions.
- `runtime/studio/shell/api.py` is the pywebview bridge between the Studio UI and Python runtime features; if it stays opaque/bytecode-centered, every new UI capability inherits testability and portability risk.
- `runtime/studio/launcher_update_check.py` affects launch/update/readiness surfaces and therefore future gateway/daemon/terminal/log-tail work.

So the bridge/modularization work is not just for Agent Control Plane. Agent Control Plane is the first pressure point because it depends on Chat, runtime status, launch controls, approvals, logs, and terminal authority all at once.

## Current live size snapshot

Observed 2026-06-11 20:35:33 BST:

```text
44713 runtime/studio/shell/frontend/app.js
 8337 runtime/studio/shell/api.py
16146 runtime/studio/launcher_update_check.py
```

## P0 governed freeze policy: stop monolith growth

### Policy location and authority

This file is the **developer execution plan** for the monolith-reduction lane. The corresponding Studio-level architecture rule is recorded in `06_AGENTS/ChaseOS-Studio-Architecture.md` under "Studio monolith freeze and facade policy" so agent harnesses have a stable ChaseOS-facing policy anchor before touching implementation files.

For implementation agents, treat this section as binding working policy for ChaseOS Studio changes until superseded by a newer Studio architecture/update record.

### Explicit engineering rule

Do not add major new feature logic directly to:

```text
runtime/studio/shell/frontend/app.js
runtime/studio/shell/api.py
runtime/studio/launcher_update_check.py
```

Allowed changes there only:

- thin adapter calls into tested/source-owned modules,
- imports or module-loading wiring,
- compatibility wrappers around existing behavior,
- bug fixes that cannot be safely isolated yet,
- comments/markers identifying extraction seams,
- deletion/replacement of a legacy slice after tests prove the new slice.

Disallowed changes there:

- new business logic,
- new persistence semantics,
- new provider/model behavior,
- new authority decisions,
- large UI rendering blocks,
- new runtime lifecycle semantics,
- provider/secret handling,
- direct canonical writeback behavior.

### Why this is P0

The monolith risk is now a ChaseOS control-plane risk, not just a code-quality issue. Continuing to add sidebar, runtime-control, launcher, provider-status, approval, or log-tail behavior directly into these files increases:

- regression risk,
- testability burden,
- context-window failure for agent harnesses,
- GUI verification burden,
- onboarding cost for Hermes/OpenClaw/Codex lanes,
- authority-boundary ambiguity,
- risk of accidental provider/secret or lifecycle behavior coupling.

The safe default is therefore: **new feature behavior must land in source-owned modules/facades with focused tests, and the large files may only load/delegate to those seams.**

## Already started

### 1. Chat sidebar model seam

```text
runtime/studio/shell/frontend/chat_sidebar_model.js
runtime/studio/shell/test_chat_sidebar_model.py
runtime/studio/shell/chat_sidebar_model_harness.js
```

Status: implemented and tested.

Purpose: pure grouping/selection/search/sort model for sidebar folders and threads.

### 2. Chat sidebar API bridge

```text
runtime/studio/shell/frontend/chat_sidebar_api.js
runtime/studio/shell/test_chat_sidebar_api.py
runtime/studio/shell/chat_sidebar_api_harness.js
```

Status: implemented and tested.

Purpose: normalized local-state-only wrapper around pywebview calls used by sidebar actions.

## Roadmap

### Phase 1 — Sidebar seam completion

Goal: finish extracting the active Chat sidebar into small tested modules before changing visual density.

#### 1.1 `chat_sidebar_view.js`

Create:

```text
runtime/studio/shell/frontend/chat_sidebar_view.js
runtime/studio/shell/test_chat_sidebar_view.py
runtime/studio/shell/chat_sidebar_view_harness.js
```

Responsibilities:

- render folder sections from `ChaseOSChatSidebarModel.buildChatSidebarModel(...)`,
- preserve existing row data attributes expected by `chat_sidebar_actions.js`,
- render empty folder state,
- render selected folder/thread state,
- never call pywebview or provider APIs.

Verification:

- Node harness asserts expected HTML/data attributes,
- existing `chat_sidebar_actions.js` can attach to rendered rows,
- focused pytest passes.

#### 1.2 Replace `_renderChatThreadNavigator(...)` body with adapter glue

Modify only the relevant slice in:

```text
runtime/studio/shell/frontend/app.js
```

New shape:

```js
const model = window.ChaseOSChatSidebarModel.buildChatSidebarModel(chatWorkspaces, state);
window.ChaseOSChatSidebarView.render(nav, model);
```

Keep route-state persistence in `app.js` until separately extracted.

Verification:

- model/view/action harnesses pass,
- existing backend thread rename tests pass,
- visual proof captures context menu and folder/thread rows.

#### 1.3 Visual density pass

Only after 1.1 and 1.2:

- collapsible nested folders,
- active folder highlight,
- folder counts,
- pinned/recent/starred sections,
- drag affordance improvements,
- keyboard navigation,
- search/filter polish,
- “New chat in selected folder.”

## Phase 2 — Python Studio API facade around legacy `api.py`

Goal: stop adding real behavior to the recovered-bytecode bridge and make Python APIs source-owned/testable.

Create package:

```text
runtime/studio/api_facade/
  __init__.py
  chat_threads.py
  runtime_controls.py
  approvals.py
  model_info.py
  audit_feed.py
  watchdog.py
  terminal.py
  voice.py
  companions.py
```

Rules:

- `runtime/studio/shell/api.py` becomes delegation/compatibility only.
- New behavior lands in `runtime/studio/api_facade/*.py` or existing source-owned modules.
- Every facade module gets direct tests that do not require the CPython 3.14 recovered core.

Initial migration order:

1. `chat_threads.py` — delegates to `phase11_chat_thread_conversations.py`.
2. `model_info.py` — delegates to `runtime_model_info.py`.
3. `audit_feed.py` — delegates to `control_plane_audit.py`.
4. `watchdog.py` — delegates to `runtime_watchdog.py`.
5. `approvals.py` — delegates to `runtime_synthesis_approval.py`.
6. `runtime_controls.py` — wraps launch/status calls.
7. `terminal.py` — terminal sessions/log-tail/stop/restart bridge.
8. `voice.py` and `companions.py` after control/status spine stabilizes.

Verification:

- facade tests pass under current Python via `uvx --from pytest pytest ...`,
- py_compile clean for new source modules,
- `api.py` diffs stay thin.

## Phase 3 — Runtime launcher/log-tail extraction

Goal: reduce `launcher_update_check.py` risk before adding more Stop/Restart/log-tail UI.

Create source-owned modules around launcher/runtime facts:

```text
runtime/studio/launcher_facade/
  __init__.py
  status_model.py
  log_tail.py
  lifecycle_actions.py
  update_readiness.py
```

First targets:

- normalize launch status,
- tail bounded logs safely,
- Stop/Restart action packets,
- watchdog status presentation,
- synthesize approval visibility without storing model keys.

Verification:

- no provider secrets in Studio process,
- Stop/Restart are approval/authority-aware,
- log-tail paths are vault-guarded,
- watchdog never auto-enables synthesis.

## Phase 4 — Unified status spine

Goal: stop Chat, Runtime Cockpit, Settings, companion, and Voice surfaces from disagreeing.

Create:

```text
runtime/studio/studio_status_model.py
runtime/studio/shell/frontend/studio_status_model.js
```

Consumers:

- Chat runtime chips,
- Runtime Cockpit,
- Settings Runtime Controls,
- Agent Control Plane card,
- Voice Mode readiness,
- companion lens.

Verification:

- one backend status fixture drives all views,
- tests assert consistent labels/state codes across surfaces.

## Phase 5 — Route/state extraction from `app.js`

Goal: remove non-rendering state logic from `app.js` after UI seams are stable.

Candidate frontend modules:

```text
runtime/studio/shell/frontend/chat_route_state.js
runtime/studio/shell/frontend/runtime_panel_state.js
runtime/studio/shell/frontend/settings_state.js
runtime/studio/shell/frontend/studio_event_bus.js
```

Verification:

- reload preserves selected chat/folder/thread,
- route state is local-state only,
- no provider/model calls,
- UI events remain backwards-compatible.

## Phase 6 — Retire/replace legacy slices gradually

Goal: make large files smaller through proven deletion, not speculative rewrite.

Process per slice:

1. write a harness around current behavior,
2. extract new source-owned module,
3. wire monolith to module,
4. run tests and visual proof,
5. delete old slice,
6. record build log.

Success metrics:

- `app.js` line count decreases across passes,
- `api.py` receives only facade delegations,
- `launcher_update_check.py` receives only compatibility glue,
- tests cover extracted module behavior,
- visual proof exists for user-facing slices.

## Immediate next pass after current work

Implement `chat_sidebar_view.js`, then replace the `_renderChatThreadNavigator(...)` internals with a thin model/view adapter.

Do not begin visual redesign until model + API + view + adapter tests are green.
