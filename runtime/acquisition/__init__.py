"""Acquisition + Normalization runtime substrate."""

__all__ = [
    "AcquisitionBuildError",
    "AcquisitionPlan",
    "AcquisitionValidationError",
    "SourcePackBuilder",
    "build_visual_capture_downstream_gate",
    "build_visual_capture_downstream_gate_policy",
    "build_visual_capture_source_pack_approval_preview",
    "build_visual_capture_source_pack_approval_preview_policy",
    "build_visual_capture_source_pack_aor_dispatch_readiness",
    "build_visual_capture_source_pack_aor_dispatch_readiness_policy",
    "build_visual_capture_source_pack_aor_dispatch_approval_design",
    "build_visual_capture_source_pack_aor_dispatch_approval_design_policy",
    "build_visual_capture_source_pack_aor_dispatch_approval_request",
    "build_visual_capture_source_pack_aor_dispatch_approval_request_writer_policy",
    "build_visual_capture_source_pack_aor_dispatch_approval_decision",
    "build_visual_capture_source_pack_aor_dispatch_approval_decision_writer_policy",
    "build_visual_capture_source_pack_aor_dispatch_approval_consumption",
    "build_visual_capture_source_pack_aor_dispatch_approval_consumption_executor_policy",
    "build_visual_capture_source_pack_aor_dispatch_agent_bus_task",
    "build_visual_capture_source_pack_aor_dispatch_agent_bus_task_claim_readiness",
    "build_visual_capture_source_pack_aor_dispatch_agent_bus_task_claim_readiness_policy",
    "build_visual_capture_source_pack_aor_dispatch_agent_bus_task_claim_executor",
    "build_visual_capture_source_pack_aor_dispatch_agent_bus_task_claim_executor_policy",
    "build_visual_capture_source_pack_aor_dispatch_agent_bus_claimed_task_aor_dry_run_readiness",
    "build_visual_capture_source_pack_aor_dispatch_agent_bus_claimed_task_aor_dry_run_readiness_policy",
    "build_visual_capture_source_pack_aor_dispatch_agent_bus_claimed_task_aor_dry_run_executor",
    "build_visual_capture_source_pack_aor_dispatch_agent_bus_claimed_task_aor_dry_run_executor_policy",
    "build_visual_capture_source_pack_aor_dispatch_agent_bus_full_dispatch_readiness",
    "build_visual_capture_source_pack_aor_dispatch_agent_bus_full_dispatch_readiness_policy",
    "build_visual_capture_source_pack_aor_dispatch_agent_bus_full_dispatch_executor",
    "build_visual_capture_source_pack_aor_dispatch_agent_bus_full_dispatch_executor_policy",
    "build_visual_capture_source_pack_sic_ingestion_readiness",
    "build_visual_capture_source_pack_sic_ingestion_readiness_policy",
    "build_visual_capture_source_pack_sic_ingestion_approval_request",
    "build_visual_capture_source_pack_sic_ingestion_approval_request_writer_policy",
    "build_visual_capture_source_pack_sic_ingestion_approval_consumption_readiness",
    "build_visual_capture_source_pack_sic_ingestion_approval_consumption_readiness_policy",
    "build_visual_capture_source_pack_sic_ingestion_approval_decision",
    "build_visual_capture_source_pack_sic_ingestion_approval_decision_writer_policy",
    "build_visual_capture_source_pack_sic_ingestion_approval_consumption",
    "build_visual_capture_source_pack_sic_ingestion_approval_consumption_executor_policy",
    "build_visual_capture_source_pack_sic_ingestion",
    "build_visual_capture_source_pack_sic_ingestion_executor_policy",
    "build_visual_capture_source_pack_sic_graph_indexing_readiness",
    "build_visual_capture_source_pack_sic_graph_indexing_readiness_policy",
    "build_visual_capture_source_pack_sic_graph_indexing_executor",
    "build_visual_capture_source_pack_sic_graph_indexing_executor_policy",
    "build_visual_capture_source_pack_canonical_promotion_readiness",
    "build_visual_capture_source_pack_canonical_promotion_readiness_policy",
    "build_visual_capture_source_pack_canonical_promotion_approval_design",
    "build_visual_capture_source_pack_canonical_promotion_approval_design_policy",
    "build_visual_capture_source_pack_canonical_promotion_approval_request",
    "build_visual_capture_source_pack_canonical_promotion_approval_request_writer_policy",
    "build_visual_capture_source_pack_canonical_promotion_approval_consumption_readiness",
    "build_visual_capture_source_pack_canonical_promotion_approval_consumption_readiness_policy",
    "build_visual_capture_source_pack_canonical_promotion_approval_decision",
    "build_visual_capture_source_pack_canonical_promotion_approval_decision_writer_policy",
    "build_visual_capture_source_pack_canonical_promotion_approval_consumption",
    "build_visual_capture_source_pack_canonical_promotion_approval_consumption_executor_policy",
    "build_visual_capture_source_pack_canonical_promotion",
    "build_visual_capture_source_pack_canonical_promotion_executor_policy",
    "build_visual_capture_source_pack_aor_dispatch_agent_bus_task_writer_policy",
    "build_visual_capture_source_pack_write_executor_policy",
    "execute_visual_capture_source_pack_write",
    "WorkflowExecutionError",
    "build_from_plan",
    "run_source_pack_builder",
    "validate_acquisition_plan",
]


def __getattr__(name: str):
    if name in {"AcquisitionBuildError", "SourcePackBuilder", "build_from_plan"}:
        from .builder import AcquisitionBuildError, SourcePackBuilder, build_from_plan

        values = {
            "AcquisitionBuildError": AcquisitionBuildError,
            "SourcePackBuilder": SourcePackBuilder,
            "build_from_plan": build_from_plan,
        }
        return values[name]
    if name in {"AcquisitionPlan", "AcquisitionValidationError", "validate_acquisition_plan"}:
        from .plan import AcquisitionPlan, AcquisitionValidationError, validate_acquisition_plan

        values = {
            "AcquisitionPlan": AcquisitionPlan,
            "AcquisitionValidationError": AcquisitionValidationError,
            "validate_acquisition_plan": validate_acquisition_plan,
        }
        return values[name]
    if name in {"WorkflowExecutionError", "run_source_pack_builder"}:
        from .source_pack_builder import WorkflowExecutionError, run_source_pack_builder

        values = {
            "WorkflowExecutionError": WorkflowExecutionError,
            "run_source_pack_builder": run_source_pack_builder,
        }
        return values[name]
    if name in {"build_visual_capture_downstream_gate", "build_visual_capture_downstream_gate_policy"}:
        from .visual_capture_downstream_gate import (
            build_visual_capture_downstream_gate,
            build_visual_capture_downstream_gate_policy,
        )

        values = {
            "build_visual_capture_downstream_gate": build_visual_capture_downstream_gate,
            "build_visual_capture_downstream_gate_policy": build_visual_capture_downstream_gate_policy,
        }
        return values[name]
    if name in {
        "build_visual_capture_source_pack_approval_preview",
        "build_visual_capture_source_pack_approval_preview_policy",
    }:
        from .visual_capture_source_pack_approval_preview import (
            build_visual_capture_source_pack_approval_preview,
            build_visual_capture_source_pack_approval_preview_policy,
        )

        values = {
            "build_visual_capture_source_pack_approval_preview": build_visual_capture_source_pack_approval_preview,
            "build_visual_capture_source_pack_approval_preview_policy": build_visual_capture_source_pack_approval_preview_policy,
        }
        return values[name]
    if name in {
        "build_visual_capture_source_pack_write_executor_policy",
        "execute_visual_capture_source_pack_write",
    }:
        from .visual_capture_source_pack_write_executor import (
            build_visual_capture_source_pack_write_executor_policy,
            execute_visual_capture_source_pack_write,
        )

        values = {
            "build_visual_capture_source_pack_write_executor_policy": build_visual_capture_source_pack_write_executor_policy,
            "execute_visual_capture_source_pack_write": execute_visual_capture_source_pack_write,
        }
        return values[name]
    if name in {
        "build_visual_capture_source_pack_aor_dispatch_readiness",
        "build_visual_capture_source_pack_aor_dispatch_readiness_policy",
    }:
        from .visual_capture_source_pack_aor_dispatch_readiness import (
            build_visual_capture_source_pack_aor_dispatch_readiness,
            build_visual_capture_source_pack_aor_dispatch_readiness_policy,
        )

        values = {
            "build_visual_capture_source_pack_aor_dispatch_readiness": build_visual_capture_source_pack_aor_dispatch_readiness,
            "build_visual_capture_source_pack_aor_dispatch_readiness_policy": build_visual_capture_source_pack_aor_dispatch_readiness_policy,
        }
        return values[name]
    if name in {
        "build_visual_capture_source_pack_aor_dispatch_approval_design",
        "build_visual_capture_source_pack_aor_dispatch_approval_design_policy",
    }:
        from .visual_capture_source_pack_aor_dispatch_approval_design import (
            build_visual_capture_source_pack_aor_dispatch_approval_design,
            build_visual_capture_source_pack_aor_dispatch_approval_design_policy,
        )

        values = {
            "build_visual_capture_source_pack_aor_dispatch_approval_design": build_visual_capture_source_pack_aor_dispatch_approval_design,
            "build_visual_capture_source_pack_aor_dispatch_approval_design_policy": build_visual_capture_source_pack_aor_dispatch_approval_design_policy,
        }
        return values[name]
    if name in {
        "build_visual_capture_source_pack_aor_dispatch_approval_request",
        "build_visual_capture_source_pack_aor_dispatch_approval_request_writer_policy",
    }:
        from .visual_capture_source_pack_aor_dispatch_approval_request_writer import (
            build_visual_capture_source_pack_aor_dispatch_approval_request,
            build_visual_capture_source_pack_aor_dispatch_approval_request_writer_policy,
        )

        values = {
            "build_visual_capture_source_pack_aor_dispatch_approval_request": build_visual_capture_source_pack_aor_dispatch_approval_request,
            "build_visual_capture_source_pack_aor_dispatch_approval_request_writer_policy": build_visual_capture_source_pack_aor_dispatch_approval_request_writer_policy,
        }
        return values[name]
    if name in {
        "build_visual_capture_source_pack_aor_dispatch_approval_decision",
        "build_visual_capture_source_pack_aor_dispatch_approval_decision_writer_policy",
    }:
        from .visual_capture_source_pack_aor_dispatch_approval_decision_writer import (
            build_visual_capture_source_pack_aor_dispatch_approval_decision,
            build_visual_capture_source_pack_aor_dispatch_approval_decision_writer_policy,
        )

        values = {
            "build_visual_capture_source_pack_aor_dispatch_approval_decision": build_visual_capture_source_pack_aor_dispatch_approval_decision,
            "build_visual_capture_source_pack_aor_dispatch_approval_decision_writer_policy": build_visual_capture_source_pack_aor_dispatch_approval_decision_writer_policy,
        }
        return values[name]
    if name in {
        "build_visual_capture_source_pack_aor_dispatch_approval_consumption",
        "build_visual_capture_source_pack_aor_dispatch_approval_consumption_executor_policy",
    }:
        from .visual_capture_source_pack_aor_dispatch_approval_consumption_executor import (
            build_visual_capture_source_pack_aor_dispatch_approval_consumption,
            build_visual_capture_source_pack_aor_dispatch_approval_consumption_executor_policy,
        )

        values = {
            "build_visual_capture_source_pack_aor_dispatch_approval_consumption": build_visual_capture_source_pack_aor_dispatch_approval_consumption,
            "build_visual_capture_source_pack_aor_dispatch_approval_consumption_executor_policy": build_visual_capture_source_pack_aor_dispatch_approval_consumption_executor_policy,
        }
        return values[name]
    if name in {
        "build_visual_capture_source_pack_aor_dispatch_agent_bus_task",
        "build_visual_capture_source_pack_aor_dispatch_agent_bus_task_writer_policy",
    }:
        from .visual_capture_source_pack_aor_dispatch_agent_bus_task_writer import (
            build_visual_capture_source_pack_aor_dispatch_agent_bus_task,
            build_visual_capture_source_pack_aor_dispatch_agent_bus_task_writer_policy,
        )

        values = {
            "build_visual_capture_source_pack_aor_dispatch_agent_bus_task": build_visual_capture_source_pack_aor_dispatch_agent_bus_task,
            "build_visual_capture_source_pack_aor_dispatch_agent_bus_task_writer_policy": build_visual_capture_source_pack_aor_dispatch_agent_bus_task_writer_policy,
        }
        return values[name]
    if name in {
        "build_visual_capture_source_pack_aor_dispatch_agent_bus_task_claim_readiness",
        "build_visual_capture_source_pack_aor_dispatch_agent_bus_task_claim_readiness_policy",
    }:
        from .visual_capture_source_pack_aor_dispatch_agent_bus_task_claim_readiness import (
            build_visual_capture_source_pack_aor_dispatch_agent_bus_task_claim_readiness,
            build_visual_capture_source_pack_aor_dispatch_agent_bus_task_claim_readiness_policy,
        )

        values = {
            "build_visual_capture_source_pack_aor_dispatch_agent_bus_task_claim_readiness": build_visual_capture_source_pack_aor_dispatch_agent_bus_task_claim_readiness,
            "build_visual_capture_source_pack_aor_dispatch_agent_bus_task_claim_readiness_policy": build_visual_capture_source_pack_aor_dispatch_agent_bus_task_claim_readiness_policy,
        }
        return values[name]
    if name in {
        "build_visual_capture_source_pack_aor_dispatch_agent_bus_task_claim_executor",
        "build_visual_capture_source_pack_aor_dispatch_agent_bus_task_claim_executor_policy",
    }:
        from .visual_capture_source_pack_aor_dispatch_agent_bus_task_claim_executor import (
            build_visual_capture_source_pack_aor_dispatch_agent_bus_task_claim_executor,
            build_visual_capture_source_pack_aor_dispatch_agent_bus_task_claim_executor_policy,
        )

        values = {
            "build_visual_capture_source_pack_aor_dispatch_agent_bus_task_claim_executor": build_visual_capture_source_pack_aor_dispatch_agent_bus_task_claim_executor,
            "build_visual_capture_source_pack_aor_dispatch_agent_bus_task_claim_executor_policy": build_visual_capture_source_pack_aor_dispatch_agent_bus_task_claim_executor_policy,
        }
        return values[name]
    if name in {
        "build_visual_capture_source_pack_aor_dispatch_agent_bus_claimed_task_aor_dry_run_readiness",
        "build_visual_capture_source_pack_aor_dispatch_agent_bus_claimed_task_aor_dry_run_readiness_policy",
    }:
        from .visual_capture_source_pack_aor_dispatch_agent_bus_claimed_task_aor_dry_run_readiness import (
            build_visual_capture_source_pack_aor_dispatch_agent_bus_claimed_task_aor_dry_run_readiness,
            build_visual_capture_source_pack_aor_dispatch_agent_bus_claimed_task_aor_dry_run_readiness_policy,
        )

        values = {
            "build_visual_capture_source_pack_aor_dispatch_agent_bus_claimed_task_aor_dry_run_readiness": build_visual_capture_source_pack_aor_dispatch_agent_bus_claimed_task_aor_dry_run_readiness,
            "build_visual_capture_source_pack_aor_dispatch_agent_bus_claimed_task_aor_dry_run_readiness_policy": build_visual_capture_source_pack_aor_dispatch_agent_bus_claimed_task_aor_dry_run_readiness_policy,
        }
        return values[name]
    if name in {
        "build_visual_capture_source_pack_aor_dispatch_agent_bus_claimed_task_aor_dry_run_executor",
        "build_visual_capture_source_pack_aor_dispatch_agent_bus_claimed_task_aor_dry_run_executor_policy",
    }:
        from .visual_capture_source_pack_aor_dispatch_agent_bus_claimed_task_aor_dry_run_executor import (
            build_visual_capture_source_pack_aor_dispatch_agent_bus_claimed_task_aor_dry_run_executor,
            build_visual_capture_source_pack_aor_dispatch_agent_bus_claimed_task_aor_dry_run_executor_policy,
        )

        values = {
            "build_visual_capture_source_pack_aor_dispatch_agent_bus_claimed_task_aor_dry_run_executor": build_visual_capture_source_pack_aor_dispatch_agent_bus_claimed_task_aor_dry_run_executor,
            "build_visual_capture_source_pack_aor_dispatch_agent_bus_claimed_task_aor_dry_run_executor_policy": build_visual_capture_source_pack_aor_dispatch_agent_bus_claimed_task_aor_dry_run_executor_policy,
        }
        return values[name]
    if name in {
        "build_visual_capture_source_pack_aor_dispatch_agent_bus_full_dispatch_readiness",
        "build_visual_capture_source_pack_aor_dispatch_agent_bus_full_dispatch_readiness_policy",
    }:
        from .visual_capture_source_pack_aor_dispatch_agent_bus_full_dispatch_readiness import (
            build_visual_capture_source_pack_aor_dispatch_agent_bus_full_dispatch_readiness,
            build_visual_capture_source_pack_aor_dispatch_agent_bus_full_dispatch_readiness_policy,
        )

        values = {
            "build_visual_capture_source_pack_aor_dispatch_agent_bus_full_dispatch_readiness": build_visual_capture_source_pack_aor_dispatch_agent_bus_full_dispatch_readiness,
            "build_visual_capture_source_pack_aor_dispatch_agent_bus_full_dispatch_readiness_policy": build_visual_capture_source_pack_aor_dispatch_agent_bus_full_dispatch_readiness_policy,
        }
        return values[name]
    if name in {
        "build_visual_capture_source_pack_aor_dispatch_agent_bus_full_dispatch_executor",
        "build_visual_capture_source_pack_aor_dispatch_agent_bus_full_dispatch_executor_policy",
    }:
        from .visual_capture_source_pack_aor_dispatch_agent_bus_full_dispatch_executor import (
            build_visual_capture_source_pack_aor_dispatch_agent_bus_full_dispatch_executor,
            build_visual_capture_source_pack_aor_dispatch_agent_bus_full_dispatch_executor_policy,
        )

        values = {
            "build_visual_capture_source_pack_aor_dispatch_agent_bus_full_dispatch_executor": build_visual_capture_source_pack_aor_dispatch_agent_bus_full_dispatch_executor,
            "build_visual_capture_source_pack_aor_dispatch_agent_bus_full_dispatch_executor_policy": build_visual_capture_source_pack_aor_dispatch_agent_bus_full_dispatch_executor_policy,
        }
        return values[name]
    if name in {
        "build_visual_capture_source_pack_sic_ingestion_readiness",
        "build_visual_capture_source_pack_sic_ingestion_readiness_policy",
    }:
        from .visual_capture_source_pack_sic_ingestion_readiness import (
            build_visual_capture_source_pack_sic_ingestion_readiness,
            build_visual_capture_source_pack_sic_ingestion_readiness_policy,
        )

        values = {
            "build_visual_capture_source_pack_sic_ingestion_readiness": build_visual_capture_source_pack_sic_ingestion_readiness,
            "build_visual_capture_source_pack_sic_ingestion_readiness_policy": build_visual_capture_source_pack_sic_ingestion_readiness_policy,
        }
        return values[name]
    if name in {
        "build_visual_capture_source_pack_sic_ingestion_approval_request",
        "build_visual_capture_source_pack_sic_ingestion_approval_request_writer_policy",
    }:
        from .visual_capture_source_pack_sic_ingestion_approval_request_writer import (
            build_visual_capture_source_pack_sic_ingestion_approval_request,
            build_visual_capture_source_pack_sic_ingestion_approval_request_writer_policy,
        )

        values = {
            "build_visual_capture_source_pack_sic_ingestion_approval_request": build_visual_capture_source_pack_sic_ingestion_approval_request,
            "build_visual_capture_source_pack_sic_ingestion_approval_request_writer_policy": build_visual_capture_source_pack_sic_ingestion_approval_request_writer_policy,
        }
        return values[name]
    if name in {
        "build_visual_capture_source_pack_sic_ingestion_approval_consumption_readiness",
        "build_visual_capture_source_pack_sic_ingestion_approval_consumption_readiness_policy",
    }:
        from .visual_capture_source_pack_sic_ingestion_approval_consumption_readiness import (
            build_visual_capture_source_pack_sic_ingestion_approval_consumption_readiness,
            build_visual_capture_source_pack_sic_ingestion_approval_consumption_readiness_policy,
        )

        values = {
            "build_visual_capture_source_pack_sic_ingestion_approval_consumption_readiness": build_visual_capture_source_pack_sic_ingestion_approval_consumption_readiness,
            "build_visual_capture_source_pack_sic_ingestion_approval_consumption_readiness_policy": build_visual_capture_source_pack_sic_ingestion_approval_consumption_readiness_policy,
        }
        return values[name]
    if name in {
        "build_visual_capture_source_pack_sic_ingestion_approval_decision",
        "build_visual_capture_source_pack_sic_ingestion_approval_decision_writer_policy",
    }:
        from .visual_capture_source_pack_sic_ingestion_approval_decision_writer import (
            build_visual_capture_source_pack_sic_ingestion_approval_decision,
            build_visual_capture_source_pack_sic_ingestion_approval_decision_writer_policy,
        )

        values = {
            "build_visual_capture_source_pack_sic_ingestion_approval_decision": build_visual_capture_source_pack_sic_ingestion_approval_decision,
            "build_visual_capture_source_pack_sic_ingestion_approval_decision_writer_policy": build_visual_capture_source_pack_sic_ingestion_approval_decision_writer_policy,
        }
        return values[name]
    if name in {
        "build_visual_capture_source_pack_sic_ingestion_approval_consumption",
        "build_visual_capture_source_pack_sic_ingestion_approval_consumption_executor_policy",
    }:
        from .visual_capture_source_pack_sic_ingestion_approval_consumption_executor import (
            build_visual_capture_source_pack_sic_ingestion_approval_consumption,
            build_visual_capture_source_pack_sic_ingestion_approval_consumption_executor_policy,
        )

        values = {
            "build_visual_capture_source_pack_sic_ingestion_approval_consumption": build_visual_capture_source_pack_sic_ingestion_approval_consumption,
            "build_visual_capture_source_pack_sic_ingestion_approval_consumption_executor_policy": build_visual_capture_source_pack_sic_ingestion_approval_consumption_executor_policy,
        }
        return values[name]
    if name in {
        "build_visual_capture_source_pack_sic_ingestion",
        "build_visual_capture_source_pack_sic_ingestion_executor_policy",
    }:
        from .visual_capture_source_pack_sic_ingestion_executor import (
            build_visual_capture_source_pack_sic_ingestion,
            build_visual_capture_source_pack_sic_ingestion_executor_policy,
        )

        values = {
            "build_visual_capture_source_pack_sic_ingestion": build_visual_capture_source_pack_sic_ingestion,
            "build_visual_capture_source_pack_sic_ingestion_executor_policy": build_visual_capture_source_pack_sic_ingestion_executor_policy,
        }
        return values[name]
    if name in {
        "build_visual_capture_source_pack_sic_graph_indexing_readiness",
        "build_visual_capture_source_pack_sic_graph_indexing_readiness_policy",
    }:
        from .visual_capture_source_pack_sic_graph_indexing_readiness import (
            build_visual_capture_source_pack_sic_graph_indexing_readiness,
            build_visual_capture_source_pack_sic_graph_indexing_readiness_policy,
        )

        values = {
            "build_visual_capture_source_pack_sic_graph_indexing_readiness": build_visual_capture_source_pack_sic_graph_indexing_readiness,
            "build_visual_capture_source_pack_sic_graph_indexing_readiness_policy": build_visual_capture_source_pack_sic_graph_indexing_readiness_policy,
        }
        return values[name]
    if name in {
        "build_visual_capture_source_pack_sic_graph_indexing_executor",
        "build_visual_capture_source_pack_sic_graph_indexing_executor_policy",
    }:
        from .visual_capture_source_pack_sic_graph_indexing_executor import (
            build_visual_capture_source_pack_sic_graph_indexing_executor,
            build_visual_capture_source_pack_sic_graph_indexing_executor_policy,
        )

        values = {
            "build_visual_capture_source_pack_sic_graph_indexing_executor": build_visual_capture_source_pack_sic_graph_indexing_executor,
            "build_visual_capture_source_pack_sic_graph_indexing_executor_policy": build_visual_capture_source_pack_sic_graph_indexing_executor_policy,
        }
        return values[name]
    if name in {
        "build_visual_capture_source_pack_canonical_promotion_readiness",
        "build_visual_capture_source_pack_canonical_promotion_readiness_policy",
    }:
        from .visual_capture_source_pack_canonical_promotion_readiness import (
            build_visual_capture_source_pack_canonical_promotion_readiness,
            build_visual_capture_source_pack_canonical_promotion_readiness_policy,
        )

        values = {
            "build_visual_capture_source_pack_canonical_promotion_readiness": build_visual_capture_source_pack_canonical_promotion_readiness,
            "build_visual_capture_source_pack_canonical_promotion_readiness_policy": build_visual_capture_source_pack_canonical_promotion_readiness_policy,
        }
        return values[name]
    if name in {
        "build_visual_capture_source_pack_canonical_promotion_approval_design",
        "build_visual_capture_source_pack_canonical_promotion_approval_design_policy",
    }:
        from .visual_capture_source_pack_canonical_promotion_approval_design import (
            build_visual_capture_source_pack_canonical_promotion_approval_design,
            build_visual_capture_source_pack_canonical_promotion_approval_design_policy,
        )

        values = {
            "build_visual_capture_source_pack_canonical_promotion_approval_design": build_visual_capture_source_pack_canonical_promotion_approval_design,
            "build_visual_capture_source_pack_canonical_promotion_approval_design_policy": build_visual_capture_source_pack_canonical_promotion_approval_design_policy,
        }
        return values[name]
    if name in {
        "build_visual_capture_source_pack_canonical_promotion_approval_request",
        "build_visual_capture_source_pack_canonical_promotion_approval_request_writer_policy",
    }:
        from .visual_capture_source_pack_canonical_promotion_approval_request_writer import (
            build_visual_capture_source_pack_canonical_promotion_approval_request,
            build_visual_capture_source_pack_canonical_promotion_approval_request_writer_policy,
        )

        values = {
            "build_visual_capture_source_pack_canonical_promotion_approval_request": build_visual_capture_source_pack_canonical_promotion_approval_request,
            "build_visual_capture_source_pack_canonical_promotion_approval_request_writer_policy": build_visual_capture_source_pack_canonical_promotion_approval_request_writer_policy,
        }
        return values[name]
    if name in {
        "build_visual_capture_source_pack_canonical_promotion_approval_consumption_readiness",
        "build_visual_capture_source_pack_canonical_promotion_approval_consumption_readiness_policy",
    }:
        from .visual_capture_source_pack_canonical_promotion_approval_consumption_readiness import (
            build_visual_capture_source_pack_canonical_promotion_approval_consumption_readiness,
            build_visual_capture_source_pack_canonical_promotion_approval_consumption_readiness_policy,
        )

        values = {
            "build_visual_capture_source_pack_canonical_promotion_approval_consumption_readiness": build_visual_capture_source_pack_canonical_promotion_approval_consumption_readiness,
            "build_visual_capture_source_pack_canonical_promotion_approval_consumption_readiness_policy": build_visual_capture_source_pack_canonical_promotion_approval_consumption_readiness_policy,
        }
        return values[name]
    if name in {
        "build_visual_capture_source_pack_canonical_promotion_approval_decision",
        "build_visual_capture_source_pack_canonical_promotion_approval_decision_writer_policy",
    }:
        from .visual_capture_source_pack_canonical_promotion_approval_decision_writer import (
            build_visual_capture_source_pack_canonical_promotion_approval_decision,
            build_visual_capture_source_pack_canonical_promotion_approval_decision_writer_policy,
        )

        values = {
            "build_visual_capture_source_pack_canonical_promotion_approval_decision": build_visual_capture_source_pack_canonical_promotion_approval_decision,
            "build_visual_capture_source_pack_canonical_promotion_approval_decision_writer_policy": build_visual_capture_source_pack_canonical_promotion_approval_decision_writer_policy,
        }
        return values[name]
    if name in {
        "build_visual_capture_source_pack_canonical_promotion_approval_consumption",
        "build_visual_capture_source_pack_canonical_promotion_approval_consumption_executor_policy",
    }:
        from .visual_capture_source_pack_canonical_promotion_approval_consumption_executor import (
            build_visual_capture_source_pack_canonical_promotion_approval_consumption,
            build_visual_capture_source_pack_canonical_promotion_approval_consumption_executor_policy,
        )

        values = {
            "build_visual_capture_source_pack_canonical_promotion_approval_consumption": build_visual_capture_source_pack_canonical_promotion_approval_consumption,
            "build_visual_capture_source_pack_canonical_promotion_approval_consumption_executor_policy": build_visual_capture_source_pack_canonical_promotion_approval_consumption_executor_policy,
        }
        return values[name]
    if name in {
        "build_visual_capture_source_pack_canonical_promotion",
        "build_visual_capture_source_pack_canonical_promotion_executor_policy",
    }:
        from .visual_capture_source_pack_canonical_promotion_executor import (
            build_visual_capture_source_pack_canonical_promotion,
            build_visual_capture_source_pack_canonical_promotion_executor_policy,
        )

        values = {
            "build_visual_capture_source_pack_canonical_promotion": build_visual_capture_source_pack_canonical_promotion,
            "build_visual_capture_source_pack_canonical_promotion_executor_policy": build_visual_capture_source_pack_canonical_promotion_executor_policy,
        }
        return values[name]
    raise AttributeError(f"module 'runtime.acquisition' has no attribute {name!r}")
