"""
runtime/gate_interface.py — Core Gate interface (the port).

ChaseOS uses dependency inversion for the Gate so the open MIT Core does not depend
on the proprietary Control Kernel:

- **Core-eligible modules import gate operations from HERE**, not from
  ``runtime.chaseos_gate`` (the proprietary Control Kernel / enforcement engine).
- At call time this delegates to a registered :class:`GateProvider`. If none is
  registered it auto-wires to ``runtime.chaseos_gate`` when that module is installed
  (the full ChaseOS / proprietary deployment). If neither is present (a pure MIT Core
  instance with no Control Kernel) it falls back to a **deny-by-default** gate, so an
  un-kerneled Core never silently permits gated operations.

The proprietary Control Kernel provides the premium implementation (commercial policy,
entitlement enforcement, tamper-evident/signed approval records, managed policy). The
generic mechanism + this port are open; the enforcement product is not.

Public functions mirror the Control Kernel's signatures so call sites are unchanged.
Stdlib only; the optional ``runtime.chaseos_gate`` import is lazy + guarded, so this
module is import-clean for Core.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable


class GateUnavailableError(RuntimeError):
    """Raised when a gate operation needs a Control Kernel that is not installed."""


@runtime_checkable
class GateProvider(Protocol):
    """The Gate port. The proprietary Control Kernel is one implementation; the Core
    deny-by-default fallback is another. Custom kernels may register their own."""

    def load_adapter_manifest(self, adapter_id: str) -> dict: ...

    def validate_manifest(self, manifest: dict) -> list[str]: ...

    def check_provenance_minimums(
        self, write_target: str, frontmatter: Optional[dict]
    ) -> tuple[bool, str]: ...

    def check_runtime_operation(self, operation: str, **kwargs: Any) -> tuple[bool, str]: ...

    def check_coordination_path(
        self, adapter_id: str, coordination_sensitive: bool, via_bus: bool,
        target_runtime: Optional[str] = None,
    ) -> tuple[bool, str]: ...

    def get_runtime_operation_approval_schema(self, operation: str, **kwargs: Any) -> Optional[dict]: ...


_provider: Optional[GateProvider] = None


def register_gate(provider: GateProvider) -> None:
    """Register the active gate provider (e.g. the proprietary Control Kernel).

    Explicit registration takes precedence over auto-wiring. Pass ``None`` semantics
    are not supported — use a provider instance.
    """
    global _provider
    _provider = provider


def clear_gate() -> None:
    """Reset the resolved provider (mainly for tests)."""
    global _provider
    _provider = None


def get_gate() -> GateProvider:
    """Return the active gate provider, resolving it on first use."""
    global _provider
    if _provider is not None:
        return _provider
    # Auto-wire to the proprietary Control Kernel if it is installed.
    try:  # pragma: no cover - exercised only when the kernel is present
        from runtime import chaseos_gate as _kernel  # type: ignore

        _provider = _ControlKernelAdapter(_kernel)
    except Exception:
        _provider = _CoreDenyByDefaultGate()
    return _provider


class _ControlKernelAdapter:
    """Adapts the proprietary ``runtime.chaseos_gate`` module to :class:`GateProvider`."""

    def __init__(self, kernel: Any) -> None:
        self._k = kernel

    def load_adapter_manifest(self, adapter_id: str) -> dict:
        return self._k.load_adapter_manifest(adapter_id)

    def validate_manifest(self, manifest: dict) -> list[str]:
        return self._k.validate_manifest(manifest)

    def check_provenance_minimums(self, write_target: str, frontmatter: Optional[dict]) -> tuple[bool, str]:
        return self._k.check_provenance_minimums(write_target, frontmatter)

    def check_runtime_operation(self, operation: str, **kwargs: Any) -> tuple[bool, str]:
        return self._k.check_runtime_operation(operation, **kwargs)

    def check_coordination_path(
        self, adapter_id: str, coordination_sensitive: bool, via_bus: bool,
        target_runtime: Optional[str] = None,
    ) -> tuple[bool, str]:
        return self._k.check_coordination_path(adapter_id, coordination_sensitive, via_bus, target_runtime)

    def get_runtime_operation_approval_schema(self, operation: str, **kwargs: Any) -> Optional[dict]:
        return self._k.get_runtime_operation_approval_schema(operation, **kwargs)


class _CoreDenyByDefaultGate:
    """Safe fallback for a pure MIT Core instance with no Control Kernel installed.

    Enforcement decisions fail closed (deny-by-default, matching the kernel's own
    philosophy). Manifest loading requires the kernel and raises a clear error.
    """

    _MSG = "ChaseOS Control Kernel is not installed — gate operation denied (Core deny-by-default fallback)."

    def load_adapter_manifest(self, adapter_id: str) -> dict:
        raise GateUnavailableError(self._MSG)

    def validate_manifest(self, manifest: dict) -> list[str]:
        return ["control-kernel-not-installed"]

    def check_provenance_minimums(self, write_target: str, frontmatter: Optional[dict]) -> tuple[bool, str]:
        return (False, self._MSG)

    def check_runtime_operation(self, operation: str, **kwargs: Any) -> tuple[bool, str]:
        return (False, self._MSG)

    def check_coordination_path(
        self, adapter_id: str, coordination_sensitive: bool, via_bus: bool,
        target_runtime: Optional[str] = None,
    ) -> tuple[bool, str]:
        # Non-coordination-sensitive work proceeds; coordination-sensitive work cannot be
        # verified without a Control Kernel, so it fails closed.
        if not coordination_sensitive:
            return (True, "coordination path not required")
        return (False, self._MSG)

    def get_runtime_operation_approval_schema(self, operation: str, **kwargs: Any) -> Optional[dict]:
        return None  # no approval schema is available without a Control Kernel


# --- module-level delegating API (mirrors runtime.chaseos_gate signatures) ----------

def load_adapter_manifest(adapter_id: str) -> dict:
    return get_gate().load_adapter_manifest(adapter_id)


def validate_manifest(manifest: dict) -> list[str]:
    return get_gate().validate_manifest(manifest)


def check_provenance_minimums(write_target: str, frontmatter: Optional[dict]) -> tuple[bool, str]:
    return get_gate().check_provenance_minimums(write_target, frontmatter)


def check_runtime_operation(operation: str, **kwargs: Any) -> tuple[bool, str]:
    return get_gate().check_runtime_operation(operation, **kwargs)


def check_coordination_path(
    adapter_id: str, coordination_sensitive: bool, via_bus: bool, target_runtime: Optional[str] = None,
) -> tuple[bool, str]:
    return get_gate().check_coordination_path(adapter_id, coordination_sensitive, via_bus, target_runtime)


def get_runtime_operation_approval_schema(operation: str, **kwargs: Any) -> Optional[dict]:
    return get_gate().get_runtime_operation_approval_schema(operation, **kwargs)


# ── ApprovalGateway port (ADR-0014) ──────────────────────────────────────────────
# Core defines the approval contract; the proprietary StudioService implements it.
# Core-MIT modules (e.g. runtime/chaser) depend on this port — NOT on runtime.studio
# — so they import cleanly with no proprietary dependency. The resolver lazily binds
# the real StudioService when present, and fails closed (deny) when it is absent.


@dataclass
class ActionSpec:
    """Specification for a gated write/action routed through the ApprovalGateway.

    Field-compatible with the Studio implementation so it serializes identically;
    Core defines the contract, Studio (proprietary) consumes/validates it.
    """

    action_type: str  # e.g. "create_file" | "write_file" | "delete_file" | "promote_quarantine" | "execute_process"
    target_path: str  # relative vault path
    content: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    submitted_by: str = "core"
    note: str = ""

    def is_delete(self) -> bool:
        return self.action_type == "delete_file"

    def is_promote(self) -> bool:
        return self.action_type == "promote_quarantine"


class ApprovalGatewayError(RuntimeError):
    """Raised when no approval gateway is available to satisfy a gated action."""


@runtime_checkable
class ApprovalGateway(Protocol):
    """Approval backend contract: queue a gated action and read its approval record."""

    def queue_for_approval(self, spec: "ActionSpec") -> Any: ...

    def get_approval(self, approval_id: str) -> Optional[Any]: ...

    def list_pending(self) -> list: ...


class _DenyApprovalGateway:
    """Fail-closed fallback when no approval backend is present (e.g. ChaseOS Core).

    Reads return empty (no records); attempts to queue raise — Core has no approval
    store, so a gated action cannot be requested without a backend.
    """

    def queue_for_approval(self, spec: "ActionSpec") -> Any:
        raise ApprovalGatewayError(
            "no approval gateway available in this edition (gated action cannot be queued)"
        )

    def get_approval(self, approval_id: str) -> Optional[Any]:
        return None

    def list_pending(self) -> list:
        return []


def get_approval_gateway(vault_root: Any, *, dry_run: bool = False) -> ApprovalGateway:
    """Return the approval backend for ``vault_root``.

    Binds the proprietary StudioService when it is importable (full monorepo); returns
    a fail-closed deny gateway when it is not (MIT Core / Chaser slice).
    """

    try:
        from runtime.studio.service import StudioService
    except ImportError:
        return _DenyApprovalGateway()
    return StudioService(vault_root, dry_run=dry_run)
