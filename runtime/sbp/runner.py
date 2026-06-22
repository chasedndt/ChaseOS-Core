"""
runner.py — Generic SBP Pipeline Stub Runner (Phase 9 Pass 1A)

Provides run_sbp_pipeline(): the generic pipeline runner used as the AOR Stage 6
fallback for any scheduled-briefing workflow that does not have a specific
instance handler registered.

In Pass 1A, this runner validates the sbp_config, collects inputs via adapters,
and produces a structured metadata output documenting what the pipeline collected
and how it would run. This proves the substrate works end-to-end without requiring
a real LLM call or instance-specific content generation.

Instance pipelines (Pass 1B+) register their own handlers using SBPBaseHandler
and provide a concrete generate_content() implementation. Once registered, the AOR
engine dispatches to them directly; the generic runner remains as a fallback for
manifests that have not yet been given a concrete handler.

Public API:
    run_sbp_pipeline(manifest, inputs, vault_root) -> dict
    SBPRunnerError
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

from .manifest import SBPConfig, load_sbp_config
from .guardrail import check_pipeline_runnable, enforce_write_scope, SBPGuardrailViolation
from .input_adapters import get_input_adapter, InputAdapterError
from .delivery_adapters import get_delivery_adapter


class SBPRunnerError(RuntimeError):
    """Fail-closed error from the generic SBP substrate runner."""


def run_sbp_pipeline(manifest: dict, inputs: dict, vault_root: Path) -> dict:
    """Generic SBP pipeline runner for scheduled-briefing workflows.

    Called by the AOR engine Stage 6 for any scheduled-briefing workflow that
    does not have a specific instance handler registered.

    Validates sbp_config, collects inputs, produces a structured metadata output,
    and invokes declared delivery adapters. Returns an AOR-compatible result dict.

    Raises SBPRunnerError on any validation or execution failure (fail-closed).
    """
    pipeline_id = manifest.get("id", "unknown")
    run_date = date.today().isoformat()

    # Step 1: Validate sbp_config block
    try:
        sbp_config: SBPConfig = load_sbp_config(manifest)
    except Exception as exc:
        raise SBPRunnerError(
            f"sbp_config validation failed for '{pipeline_id}': {exc}"
        ) from exc

    # Step 2: Guardrail check — fail-closed
    try:
        check_pipeline_runnable(sbp_config)
    except SBPGuardrailViolation as exc:
        raise SBPRunnerError(
            f"guardrail check failed for '{pipeline_id}': {exc}"
        ) from exc

    # Step 3: Collect inputs via declared adapters
    collected: dict = {}
    adapter_summary: list[dict] = []
    for adapter_cfg in sbp_config.input_adapters:
        try:
            adapter = get_input_adapter(adapter_cfg.type)
            result = adapter.collect(adapter_cfg, vault_root)
            collected[adapter_cfg.type] = result
            adapter_summary.append({
                "type": adapter_cfg.type,
                "trust_tier": result.get("trust_tier", adapter_cfg.trust_tier),
                "source_count": len(result.get("sources", [])),
                "stub": result.get("stub", False),
            })
        except InputAdapterError as exc:
            raise SBPRunnerError(
                f"input adapter '{adapter_cfg.type}' failed for '{pipeline_id}': {exc}"
            ) from exc
        except Exception as exc:
            raise SBPRunnerError(
                f"input adapter '{adapter_cfg.type}' raised unexpected error for "
                f"'{pipeline_id}': {exc}"
            ) from exc

    # Step 4: Generate stub content (substrate-proof output for Pass 1A)
    content = _render_stub_output(pipeline_id, run_date, sbp_config, adapter_summary)

    # Step 5: Determine writeback path
    writeback_targets: list[str] = manifest.get("writeback_targets", ["07_LOGS/SBP-Runs/"])
    first_target = writeback_targets[0].rstrip("/")
    relative_output_path = f"{first_target}/{run_date}-{pipeline_id}-run.md"

    # Step 6: Enforce write scope
    effective_scope = sbp_config.guardrail.write_scope or writeback_targets
    try:
        enforce_write_scope(relative_output_path, effective_scope)
    except SBPGuardrailViolation as exc:
        raise SBPRunnerError(
            f"write scope violation for '{pipeline_id}': {exc}"
        ) from exc

    # Step 7: Invoke declared delivery adapters
    delivery_results: list[dict] = []
    for delivery_cfg in sbp_config.delivery_adapters:
        try:
            adapter = get_delivery_adapter(delivery_cfg.type)
            delivery_context = {
                "vault_root": str(vault_root),
                "pipeline_id": pipeline_id,
                "date": run_date,
                "channel_hint": delivery_cfg.channel_hint,
                "webhook_env_var": delivery_cfg.webhook_env_var,
                "channel_id": delivery_cfg.channel_id,
            }
            result = adapter.deliver(content, delivery_context)
            delivery_results.append({"type": delivery_cfg.type, **result})
        except Exception as exc:
            delivery_results.append({
                "type": delivery_cfg.type,
                "success": False,
                "details": f"delivery adapter error: {exc}",
            })

    return {
        "handler_status": "executed",
        "workflow_id": pipeline_id,
        "date": run_date,
        "sbp_mode": "substrate-stub",
        "input_adapters_used": [a["type"] for a in adapter_summary],
        "adapter_summary": adapter_summary,
        "delivery_results": delivery_results,
        "writebacks": [
            {
                "path": relative_output_path,
                "content": content,
                "content_type": "text/markdown",
            }
        ],
    }


def _render_stub_output(
    pipeline_id: str,
    run_date: str,
    sbp_config: SBPConfig,
    adapter_summary: list[dict],
) -> str:
    """Render a structured metadata document for the generic stub runner."""
    generated_at = datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()
    lines = [
        "---",
        "type: sbp-run-stub",
        f"pipeline_id: {pipeline_id}",
        f"date: {run_date}",
        f"generated_at: {generated_at}",
        "sbp_mode: substrate-stub",
        "---",
        "",
        f"# SBP Run — {pipeline_id} — {run_date}",
        "",
        "> **This is a substrate-stub output.** This pipeline ran through the generic SBP runner.",
        "> To produce real content, register an instance handler that extends SBPBaseHandler",
        "> and implements generate_content(). See SBP Pass 1B for the first instance pipeline.",
        "",
        "---",
        "",
        "## Pipeline Configuration",
        "",
        f"- **Pipeline ID:** `{pipeline_id}`",
        f"- **Trigger type:** `{sbp_config.trigger.type}`",
        f"- **Execution adapter:** `{sbp_config.execution_adapter}`",
        f"- **Permission ceiling:** `{sbp_config.guardrail.permission_ceiling}`",
        f"- **Human in loop:** `{sbp_config.guardrail.human_in_loop}`",
        f"- **Fail behavior:** `{sbp_config.guardrail.fail_behavior}`",
        "",
        "---",
        "",
        "## Input Adapters Resolved",
        "",
    ]

    if adapter_summary:
        for a in adapter_summary:
            stub_note = " [STUB — not yet implemented]" if a.get("stub") else ""
            lines.append(
                f"- `{a['type']}`: trust_tier={a['trust_tier']}, "
                f"sources={a['source_count']}{stub_note}"
            )
    else:
        lines.append("- No input adapters declared.")

    lines.extend([
        "",
        "---",
        "",
        "## Delivery Adapters Declared",
        "",
    ])

    if sbp_config.delivery_adapters:
        for da in sbp_config.delivery_adapters:
            lines.append(f"- `{da.type}`")
    else:
        lines.append("- No delivery adapters declared.")

    lines.extend([
        "",
        "---",
        "",
        "*SBP substrate stub output — written to vault log only — not canonical state*",
        "*Implement generate_content() in an SBPBaseHandler subclass to produce real output.*",
    ])

    return "\n".join(lines)
