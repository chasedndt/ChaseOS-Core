"""
runtime.operator_surface.scopes

Scope validation helpers and scope enforcement utilities.
The FSOS executor uses these to validate scope before dispatch
and enforce scope at action time.

Scope model defined in: 06_AGENTS/Full-System-Operator-Surface.md Section 6.1
"""

from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse

from runtime.operator_surface.contracts import OperatorScope
from runtime.operator_surface.capabilities import SurfaceType


class ScopeViolation(Exception):
    """Raised when an action exceeds the declared scope."""
    pass


def validate_scope(scope: OperatorScope) -> list[str]:
    """
    Validate an OperatorScope before dispatch.
    Returns list of error strings; empty = valid.
    """
    return scope.validate()


def check_uri_allowed(uri: str, scope: OperatorScope) -> bool:
    """
    Return True if uri is within the declared scope.
    For browser: checks target_uris and allowed_origins.
    For filesystem: checks allowed_paths.
    Raises ScopeViolation if the uri is in forbidden_zones.
    """
    # Forbidden zones are absolute — check first
    for zone in scope.forbidden_zones:
        if uri.startswith(zone) or uri == zone:
            raise ScopeViolation(
                f"URI '{uri}' is in a forbidden zone: '{zone}'. "
                "Action blocked by FSOS scope enforcement."
            )

    # Check explicit target_uris
    if uri in scope.target_uris:
        return True

    # For browser surface: check allowed_origins
    if scope.surface == SurfaceType.BROWSER and scope.allowed_origins:
        try:
            parsed = urlparse(uri)
            host = parsed.netloc.lstrip("www.")
            for origin in scope.allowed_origins:
                allowed_host = origin.lstrip("www.").lstrip("https://").lstrip("http://")
                if host == allowed_host or host.endswith("." + allowed_host):
                    return True
        except Exception:
            return False

    # For filesystem surface: check allowed_paths prefix
    if scope.surface == SurfaceType.FILESYSTEM and scope.allowed_paths:
        for allowed in scope.allowed_paths:
            if uri.startswith(allowed):
                return True

    return False


def enforce_uri_in_scope(uri: str, scope: OperatorScope, action_type: str) -> None:
    """
    Assert that a URI is within scope.
    Raises ScopeViolation if not.
    Used by adapter execute_step() before taking action.
    """
    if not check_uri_allowed(uri, scope):
        raise ScopeViolation(
            f"Action '{action_type}' on URI '{uri}' is outside declared scope. "
            f"Allowed origins: {scope.allowed_origins}, "
            f"Allowed paths: {scope.allowed_paths}, "
            f"Target URIs: {scope.target_uris}. "
            "Execution halted."
        )


def check_action_limit(scope: OperatorScope, actions_taken: int) -> None:
    """
    Check whether the action limit has been reached.
    Raises ScopeViolation if at or over ceiling.
    """
    if actions_taken >= scope.max_actions:
        raise ScopeViolation(
            f"Action limit reached: {actions_taken}/{scope.max_actions}. "
            "Execution halted per scope ceiling."
        )


def action_requires_approval(action_class: str, scope: OperatorScope) -> bool:
    """
    Return True if this action class requires an approval gate.
    Checks scope.requires_approval list.
    """
    return action_class in scope.requires_approval


def approval_required_actions_for(surface: SurfaceType, adapter=None) -> frozenset[str]:
    """
    Return the effective always-approval action set for a surface.

    This combines the shared FSOS surface defaults with any adapter-specific
    hard requirements exposed as APPROVAL_REQUIRED_ACTIONS.
    """
    required = set(SURFACE_DEFAULT_APPROVALS.get(surface, frozenset()))
    if adapter is not None:
        adapter_required = getattr(adapter, "APPROVAL_REQUIRED_ACTIONS", frozenset())
        required.update(adapter_required or [])
    return frozenset(required)


# ── Default approval-required actions per surface ────────────────────────
# These are the absolute defaults from Full-System-Operator-Safety-SOP.md
# Adapters may add additional surface-specific requirements.

BROWSER_ALWAYS_APPROVAL_REQUIRED = frozenset({
    "form_submit",
    "credential_field_fill",
    "file_download",
    "navigate_external_domain",
})

TERMINAL_ALWAYS_APPROVAL_REQUIRED = frozenset({
    "destructive_command",
    "write_command",
    "network_command",
})

DESKTOP_ALWAYS_APPROVAL_REQUIRED = frozenset({
    "write_action",
    "window_close",
    "application_launch",
})

FILESYSTEM_ALWAYS_APPROVAL_REQUIRED = frozenset({
    "file_delete",      # always — no exceptions
    "file_write",
    "file_move",
    "cross_repo_copy",
})

SURFACE_DEFAULT_APPROVALS: dict[SurfaceType, frozenset] = {
    SurfaceType.BROWSER: BROWSER_ALWAYS_APPROVAL_REQUIRED,
    SurfaceType.TERMINAL: TERMINAL_ALWAYS_APPROVAL_REQUIRED,
    SurfaceType.DESKTOP: DESKTOP_ALWAYS_APPROVAL_REQUIRED,
    SurfaceType.FILESYSTEM: FILESYSTEM_ALWAYS_APPROVAL_REQUIRED,
}
