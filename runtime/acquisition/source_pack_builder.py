"""AOR entrypoint for Acquisition + Normalization source-pack building.

This module preserves the existing `run_source_pack_builder` workflow handler
while delegating implementation to the Pass 1A generic substrate:

- `plan.py` validates acquisition plans
- `adapters/local.py` reads declared local inputs only
- `builder.py` creates source_packet, normalized_source_pack, and
  briefing_ready_input_set artifacts
- `validators.py` enforces provenance, trust, freshness, and no canonical
  mutation
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .builder import AcquisitionBuildError, SourcePackBuilder
from .plan import acquisition_plan_from_legacy_inputs
from .validators import AcquisitionValidationError


class WorkflowExecutionError(RuntimeError):
    """Fail-closed workflow error for acquisition normalization."""


def run_source_pack_builder(inputs: dict[str, Any], vault_root: Path) -> dict[str, Any]:
    """Build first-wave Acquisition + Normalization artifacts for AOR.

    No connectors, browser automation, delivery, cron, MCP expansion, or
    canonical mutation are performed.
    """
    try:
        plan = acquisition_plan_from_legacy_inputs(inputs)
        result = SourcePackBuilder().build(plan=plan, vault_root=vault_root)
    except (AcquisitionValidationError, AcquisitionBuildError) as exc:
        raise WorkflowExecutionError(f"source_pack_builder: {exc}") from exc

    objective = {
        "title": plan.objective,
        "requested_by": plan.requested_by,
        "downstream_target": plan.downstream_target,
    }
    return result.to_aor_result(
        objective=objective,
        project_scope=str(inputs.get("project_scope") or ""),
    )
