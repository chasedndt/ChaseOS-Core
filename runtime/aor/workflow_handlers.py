"""
runtime/aor/workflow_handlers.py — AOR workflow-handler registry (ADR-0015).

Dependency inversion for the engine's workflow dispatch: the engine resolves handlers
**only through this registry** and never names a concrete workflow module. Each handler
is registered with a lazy loader (imports happen on resolve, not at registry import) and
a **tier** that declares the Core/instance boundary as data:

- ``core``     — generic framework workflows (ship in MIT Core)
- ``runtime``  — generic coordination over MIT third-party runtimes (Core-eligible)
- ``shadow``   — dev/research shadows (Core dev tools)
- ``instance`` — personal/business-instance workflows (defined by the operator's own
                 instance) — **never ship in Core**

The Core/instance *split* (registering the instance tier from a monorepo-only module)
is a later migration step; today all tiers register here for behaviour parity with the
former 27-branch ``if``-chain. See ADR-0015 for the migration plan.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any, Callable, Optional

CORE = "core"
RUNTIME = "runtime"
SHADOW = "shadow"
INSTANCE = "instance"
_TIERS = frozenset({CORE, RUNTIME, SHADOW, INSTANCE})

# workflow_id -> (lazy loader returning the handler callable, tier)
_REGISTRY: dict[str, tuple[Callable[[], Any], str]] = {}

# Instance-tier workflows (defined by the operator's own instance) are registered
# by an optional, monorepo-only pack — NOT by this Core module. The pack is loaded
# lazily and fail-open: in the MIT Core export the module is absent, the import fails
# gracefully, and only core/runtime/shadow workflows resolve. In the full instance the
# pack is present and registers the instance tier on first lookup. (ADR-0015)
_INSTANCE_PACK = "runtime.aor.workflow_handlers_instance"
_instance_pack_loaded = False


def _ensure_instance_pack_loaded() -> None:
    global _instance_pack_loaded
    if _instance_pack_loaded:
        return
    _instance_pack_loaded = True  # set first: a missing/broken pack must not retry every call
    try:
        import_module(_INSTANCE_PACK)  # registers instance-tier handlers as a side effect
    except ImportError:
        pass  # Core export: instance pack absent → only core/runtime/shadow resolve


class WorkflowHandlerError(RuntimeError):
    """Invalid workflow-handler registration."""


def _loader(module: str, attr: str) -> Callable[[], Any]:
    """Build a lazy loader that imports ``module`` and returns its ``attr`` on call."""
    def _load() -> Any:
        return getattr(import_module(module), attr)
    return _load


def register(workflow_id: str, loader: Callable[[], Any], *, tier: str) -> None:
    """Register a handler loader under ``workflow_id`` with a Core/instance ``tier``.
    Re-registering the same id replaces it (lets an instance bootstrap override)."""
    if not workflow_id:
        raise WorkflowHandlerError("workflow_id is required")
    if tier not in _TIERS:
        raise WorkflowHandlerError(f"unknown tier {tier!r}; expected one of {sorted(_TIERS)}")
    if not callable(loader):
        raise WorkflowHandlerError("loader must be callable")
    _REGISTRY[workflow_id] = (loader, tier)


def resolve(workflow_id: str) -> Optional[Any]:
    """Return the handler callable for ``workflow_id`` (importing it lazily), or
    ``None`` if unregistered — matching the engine's former dispatch semantics."""
    _ensure_instance_pack_loaded()
    entry = _REGISTRY.get(workflow_id)
    if entry is None:
        return None
    return entry[0]()


def tier_of(workflow_id: str) -> Optional[str]:
    _ensure_instance_pack_loaded()
    entry = _REGISTRY.get(workflow_id)
    return entry[1] if entry else None


def registered_ids(*, tier: Optional[str] = None) -> list[str]:
    _ensure_instance_pack_loaded()
    if tier is None:
        return sorted(_REGISTRY)
    return sorted(wid for wid, (_, t) in _REGISTRY.items() if t == tier)


def _register_defaults() -> None:
    """Register the full handler set (parity with the former engine if-chain)."""
    # ── core: generic framework workflows ────────────────────────────────────────
    register("operator_today", _loader("runtime.workflows.operator_today", "run_operator_today"), tier=CORE)
    register("operator_close_day", _loader("runtime.workflows.operator_close_day", "run_operator_close_day"), tier=CORE)
    register("graph_hygiene", _loader("runtime.workflows.graph_hygiene", "run_graph_hygiene"), tier=CORE)
    register("graduate_ideas", _loader("runtime.workflows.graduate_ideas", "run_graduate_ideas"), tier=CORE)
    register("browser_research", _loader("runtime.workflows.browser_research", "run_browser_research"), tier=CORE)
    register("source_pack_builder", _loader("runtime.acquisition.source_pack_builder", "run_source_pack_builder"), tier=CORE)
    register("trace_idea", _loader("runtime.workflows.trace_idea", "run_trace_idea"), tier=CORE)
    register("meeting_ingest_linker", _loader("runtime.workflows.meeting_ingest_linker", "run_meeting_ingest_linker"), tier=CORE)
    register("behavior_tripwire_scan", _loader("runtime.workflows.behavior_tripwire_scan", "run_behavior_tripwire_scan"), tier=CORE)
    # NOTE: drift_scan + os_hygiene_graph are INSTANCE-tier — they embed the operator's
    # personal domain/project taxonomy (drift_scan._DOMAINS) and instance-specific vault
    # hygiene routing (cli/vault_hygiene). Registered in workflow_handlers_instance.py.

    # ── runtime: generic coordination over MIT third-party runtimes (Core-clean only) ──
    # Only bus-free runtime handlers stay in Core. The Agent Bus coordination layer
    # (runtime/agent_bus/, runtime/lifecycle/) is exclude_always — so the bus-polling
    # handlers (hermes_watch/openclaw_watch/archon_watch/openclaw_post_review_task/
    # hermes_review_execute) are INSTANCE-tier (registered in workflow_handlers_instance.py).
    register("hermes_research_synthesis", _loader("runtime.workflows.hermes_research_synthesis", "run_hermes_research_synthesis"), tier=RUNTIME)
    register("hermes_skill_review", _loader("runtime.workflows.hermes_skill_review", "run_hermes_skill_review"), tier=RUNTIME)

    # ── shadow/dev: research shadows ─────────────────────────────────────────────
    register("developer_repo_explain_shadow", _loader("runtime.aor.developer_shadow", "run_developer_repo_explain"), tier=SHADOW)
    register("hermes_operator_today_shadow", _loader("runtime.aor.hermes_shadow", "run_hermes_operator_today_shadow"), tier=SHADOW)
    register("openai_operator_research_shadow", _loader("runtime.workflows.openai_shadow", "run_openai_operator_research_shadow"), tier=SHADOW)

    # NOTE: instance-tier workflows (operator-instance-specific) are NOT
    # registered here. They live in the optional monorepo-only pack loaded by
    # _ensure_instance_pack_loaded() (see workflow_handlers_instance.py). Core ships
    # this module without that pack, so only core/runtime/shadow resolve. (ADR-0015)


_register_defaults()
