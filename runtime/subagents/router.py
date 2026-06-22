"""Runtime routing for task-scoped sub-agent presets."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from runtime.agent_bus.capabilities import load_all_capabilities

from .models import (
    RETIRED_RUNTIME_BACKENDS,
    RUNTIME_BACKEND_TO_BUS_NAME,
    SubAgentPreset,
)


@dataclass(frozen=True)
class RuntimeRoute:
    selected_runtime: str
    selected_bus_name: str
    fallback_runtimes: tuple[str, ...]
    unavailable_preferences: tuple[str, ...]
    blocked_reasons: tuple[str, ...]
    route_status: str

    @property
    def is_routable(self) -> bool:
        return self.route_status == "selected"


def build_runtime_availability(vault_root: str | Path | None = None) -> dict[str, dict[str, Any]]:
    root = Path(vault_root) if vault_root is not None else Path.cwd()
    availability: dict[str, dict[str, Any]] = {}
    try:
        capabilities = load_all_capabilities(root)
    except Exception as exc:  # pragma: no cover - defensive fallback
        return {
            "OpenHuman": {
                "registered": False,
                "retired": True,
                "reason": RETIRED_RUNTIME_BACKENDS["OpenHuman"],
            },
            "_error": {"registered": False, "reason": str(exc)},
        }
    for runtime_name, capability in capabilities.items():
        bus_name = capability.bus_name or runtime_name
        availability[bus_name] = {
            "registered": True,
            "retired": False,
            "runtime_name": runtime_name,
            "task_types": tuple(handle.task_type for handle in capability.handles),
            "max_concurrent_tasks": capability.max_concurrent_tasks,
        }
    availability["OpenHuman"] = {
        "registered": False,
        "retired": True,
        "reason": RETIRED_RUNTIME_BACKENDS["OpenHuman"],
    }
    return availability


class SubAgentRuntimeRouter:
    """Selects a current repo-supported runtime backend without executing work."""

    def __init__(
        self,
        *,
        vault_root: str | Path | None = None,
        availability: Mapping[str, Mapping[str, Any]] | None = None,
        include_retired: bool = False,
    ) -> None:
        self.vault_root = Path(vault_root) if vault_root is not None else Path.cwd()
        self.availability = {
            str(name): dict(value)
            for name, value in (
                availability if availability is not None else build_runtime_availability(self.vault_root)
            ).items()
        }
        self.include_retired = include_retired

    def select_runtime(self, preset: SubAgentPreset) -> RuntimeRoute:
        unavailable: list[str] = []
        blocked_reasons: list[str] = []
        fallbacks: list[str] = []
        for runtime in preset.runtime_preferences:
            bus_name = RUNTIME_BACKEND_TO_BUS_NAME[runtime]
            runtime_info = self.availability.get(bus_name, {})
            if runtime in RETIRED_RUNTIME_BACKENDS and not self.include_retired:
                reason = RETIRED_RUNTIME_BACKENDS[runtime]
                unavailable.append(runtime)
                blocked_reasons.append(f"{runtime}: {reason}")
                continue
            if runtime_info.get("registered") is True:
                return RuntimeRoute(
                    selected_runtime=runtime,
                    selected_bus_name=bus_name,
                    fallback_runtimes=tuple(fallbacks),
                    unavailable_preferences=tuple(unavailable),
                    blocked_reasons=tuple(blocked_reasons),
                    route_status="selected",
                )
            unavailable.append(runtime)
            blocked_reasons.append(f"{runtime}: bus runtime {bus_name!r} is not registered")
            fallbacks.append(runtime)
        return RuntimeRoute(
            selected_runtime="",
            selected_bus_name="",
            fallback_runtimes=tuple(fallbacks),
            unavailable_preferences=tuple(unavailable),
            blocked_reasons=tuple(blocked_reasons or ["no runtime preference is available"]),
            route_status="blocked",
        )
