"""
guardrail.py — SBP Guardrail Profile Enforcement (Phase 9 Pass 1A)

Enforces write scope and pipeline runnability for Scheduled Briefing Pipelines.
All enforcement is fail-closed: violations raise SBPGuardrailViolation before
any write or delivery occurs.

Public API:
    enforce_write_scope(relative_path, write_scope) -> None
    check_pipeline_runnable(sbp_config) -> None
    SBPGuardrailViolation
"""

from __future__ import annotations

from .manifest import SBPConfig, FORBIDDEN_PERMISSION_CEILINGS


class SBPGuardrailViolation(RuntimeError):
    """Raised when a guardrail boundary is violated. Fail-closed."""


def enforce_write_scope(relative_path: str, write_scope: list[str]) -> None:
    """Raise SBPGuardrailViolation if write path is outside declared write scope.

    Compares normalized relative path against each declared scope prefix.
    Empty write_scope means unconstrained — any path is allowed.
    Callers should prefer explicit write scopes over empty ones.
    """
    if not write_scope:
        return

    normalized = relative_path.replace("\\", "/").lstrip("/")
    for scope_entry in write_scope:
        scope_norm = scope_entry.replace("\\", "/").rstrip("/")
        if normalized.startswith(scope_norm):
            return

    raise SBPGuardrailViolation(
        f"write path '{relative_path}' is outside declared write scope {write_scope}; "
        f"SBP pipelines may only write to declared scopes — escalating"
    )


def check_pipeline_runnable(sbp_config: SBPConfig) -> None:
    """Validate guardrail profile permits execution. Fail-closed.

    Raises SBPGuardrailViolation if the pipeline should not run.
    """
    ceiling = sbp_config.guardrail.permission_ceiling
    if ceiling in FORBIDDEN_PERMISSION_CEILINGS:
        raise SBPGuardrailViolation(
            f"pipeline declares forbidden permission_ceiling '{ceiling}'; "
            f"SBP pipelines may not request: {sorted(FORBIDDEN_PERMISSION_CEILINGS)}"
        )

    if not sbp_config.guardrail.audit_required:
        raise SBPGuardrailViolation(
            "sbp_config.guardrail.audit_required=false is not permitted; "
            "all SBP pipeline runs must produce an audit record"
        )
