"""
base_handler.py — SBP Base Handler Pattern (Phase 9 Pass 1A)

Defines SBPBaseHandler: the abstract base class for all Scheduled Briefing
Pipeline instance handlers.

Instance handlers subclass SBPBaseHandler and implement generate_content().
The base class provides the full pipeline orchestration:
  1. sbp_config validation (from AOR manifest)
  2. Guardrail enforcement (fail-closed)
  3. Input collection via declared input adapters
  4. Content generation hook (abstract — instance-specific)
  5. Write scope enforcement
  6. Writeback preparation (returns AOR-compatible writebacks list)
  7. Delivery adapter invocation

Usage:
    class MyDigestHandler(SBPBaseHandler):
        workflow_id = "my_digest_pipeline"

        def generate_content(self, collected_inputs, vault_root):
            vault_notes = collected_inputs.get("vault-notes", {})
            content = vault_notes.get("content") or ""
            return f"## {self.workflow_id} Digest\\n\\n{content[:500]}"

    # AOR engine dispatch call:
    def run_my_digest_pipeline(inputs, vault_root, manifest=None):
        if manifest is None:
            raise SBPWorkflowExecutionError("manifest required for SBP dispatch")
        handler = MyDigestHandler()
        return handler.run(manifest, inputs, vault_root)

Public API:
    SBPBaseHandler (abstract)
    SBPWorkflowExecutionError
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from pathlib import Path

from .manifest import SBPConfig, load_sbp_config
from .guardrail import check_pipeline_runnable, enforce_write_scope, SBPGuardrailViolation
from .input_adapters import get_input_adapter, InputAdapterError
from .delivery_adapters import get_delivery_adapter


class SBPWorkflowExecutionError(RuntimeError):
    """Fail-closed execution error for SBP pipeline handlers."""


class SBPBaseHandler(ABC):
    """Abstract base for Scheduled Briefing Pipeline instance handlers.

    Subclasses must set workflow_id and implement generate_content().
    The base provides: sbp_config validation, guardrail enforcement, input
    collection, writeback preparation, and delivery adapter invocation.
    """
    workflow_id: str = ""

    @abstractmethod
    def generate_content(
        self, collected_inputs: dict, vault_root: Path, *, sbp_config: "SBPConfig | None" = None
    ) -> str:
        """Generate briefing content from collected inputs.

        collected_inputs: dict keyed by adapter type (e.g. 'vault-notes'),
                          value is the adapter's collect() output dict.
        vault_root: Path to the vault root.
        sbp_config: the validated SBPConfig for this pipeline run — provides
                    execution_adapter, guardrail, and other pipeline-level settings.
        Returns: markdown string — the pipeline's primary output artifact.
        """
        ...

    def run(self, manifest: dict, inputs: dict, vault_root: Path) -> dict:
        """Execute the full SBP pipeline. Returns AOR-compatible result dict."""
        pipeline_id = manifest.get("id", self.workflow_id or "unknown")
        run_date = date.today().isoformat()

        # Stage 1: Validate sbp_config
        try:
            sbp_config: SBPConfig = load_sbp_config(manifest)
        except Exception as exc:
            raise SBPWorkflowExecutionError(
                f"sbp_config validation failed for '{pipeline_id}': {exc}"
            ) from exc

        # Stage 2: Guardrail check — fail-closed before any execution
        try:
            check_pipeline_runnable(sbp_config)
        except SBPGuardrailViolation as exc:
            raise SBPWorkflowExecutionError(
                f"guardrail check failed for '{pipeline_id}': {exc}"
            ) from exc

        # Stage 3: Collect inputs via declared adapters
        collected: dict = {}
        for adapter_cfg in sbp_config.input_adapters:
            try:
                adapter = get_input_adapter(adapter_cfg.type)
                result = adapter.collect(adapter_cfg, vault_root)
                collected[adapter_cfg.type] = result
            except InputAdapterError as exc:
                raise SBPWorkflowExecutionError(
                    f"input adapter '{adapter_cfg.type}' failed for '{pipeline_id}': {exc}"
                ) from exc
            except Exception as exc:
                raise SBPWorkflowExecutionError(
                    f"input adapter '{adapter_cfg.type}' raised unexpected error: {exc}"
                ) from exc

        # Stage 4: Generate content (instance-specific)
        try:
            content = self.generate_content(collected, vault_root, sbp_config=sbp_config)
        except SBPWorkflowExecutionError:
            raise
        except Exception as exc:
            raise SBPWorkflowExecutionError(
                f"generate_content raised in '{pipeline_id}': {exc}"
            ) from exc

        # Stage 5: Determine writeback path
        writeback_targets: list[str] = manifest.get("writeback_targets", ["07_LOGS/SBP-Runs/"])
        first_target = writeback_targets[0].rstrip("/")
        relative_output_path = f"{first_target}/{run_date}-{pipeline_id}-run.md"

        # Stage 6: Enforce write scope
        effective_scope = sbp_config.guardrail.write_scope or writeback_targets
        try:
            enforce_write_scope(relative_output_path, effective_scope)
        except SBPGuardrailViolation as exc:
            raise SBPWorkflowExecutionError(
                f"write scope violation in '{pipeline_id}': {exc}"
            ) from exc

        # Stage 7: Invoke delivery adapters
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
                    "draft_review_required": delivery_cfg.draft_review_required,
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
            "input_adapters_used": [a.type for a in sbp_config.input_adapters],
            "delivery_results": delivery_results,
            "writebacks": [
                {
                    "path": relative_output_path,
                    "content": content,
                    "content_type": "text/markdown",
                }
            ],
        }
