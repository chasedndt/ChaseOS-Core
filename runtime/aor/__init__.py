"""
ChaseOS AOR — Autonomous Operator Runtime (Phase 9)

Bounded execution runtime for scheduled and operator-triggered workflows.
No ambient vault access. Gate rules apply to all writeback.
No workflow runs without a registry entry.

Public API:
    run_workflow(workflow_id, inputs, vault_root=None, dry_run=False) -> AORRunResult
    load_manifest(workflow_id, vault_root=None) -> dict | None
    list_manifests(vault_root=None) -> list[dict]
    load_card(card_id, vault_root=None) -> dict | None
    list_cards(vault_root=None) -> list[dict]
    classify(task_type_id, vault_root=None) -> dict

Phase 9 — ChaseOS Connector/Capture Automation successor layer.
"""

from .engine import run_workflow, AORRunResult
from .registry import load_manifest, list_manifests
from .role_cards import load_card, list_cards
from .task_router import classify, list_task_types

__all__ = [
    "run_workflow",
    "AORRunResult",
    "load_manifest",
    "list_manifests",
    "load_card",
    "list_cards",
    "classify",
    "list_task_types",
]
