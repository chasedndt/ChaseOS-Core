"""
runtime/commerce/insights.py — Insights aggregation (ADR-0004/0005).

Basic Insights (free for all plans, handover 02 §5): raw token/tool/run usage from
the append-only usage store. Advanced Insights (trends/breakdowns/forecasts/exports)
is gated by the entitlement resolver and returns an upgrade target when denied.

Read-only over runtime/commerce/store.py. Estimated retail cost requires a per-meter
rate card (deferred to Phase E) — basic Insights honestly surfaces raw quantities +
any recorded provider cost, not a retail estimate.
"""

from __future__ import annotations

from typing import Any, Optional

from runtime.commerce import entitlements, store

_BASIC_METERS = ("model_input_tokens", "model_output_tokens", "gateway_tool_calls", "workflow_runs")


def basic_insights(
    db_path,
    *,
    account_id: Optional[str] = None,
    since: Optional[str] = None,
) -> dict[str, Any]:
    """Free basic usage visibility. Always available (no entitlement gate)."""
    totals = store.aggregate_usage(db_path, account_id=account_id, since=since)
    return {
        "tier": "basic",
        "account_id": account_id,
        "since": since,
        "meters": {m: totals.get(m, "0") for m in _BASIC_METERS},
        "all_meters": totals,
        "provider_cost_minor": store.provider_cost_total(db_path),
        "currency": "GBP",
        "estimated_retail_cost": None,
        "note": "Basic Insights is free for all plans. Retail cost estimate requires the Phase E rate card.",
    }


def advanced_insights(
    db_path,
    *,
    plan_id: str,
    account_id: Optional[str] = None,
    since: Optional[str] = None,
    grants: Optional[list[dict]] = None,
) -> dict[str, Any]:
    """Advanced Insights — entitlement-gated on `studio.insights.advanced`.

    Returns either the analysis payload (when entitled) or the entitlement-denial
    contract object with an upgrade_target.
    """
    ent = entitlements.check(plan_id, "studio.insights.advanced", grants=grants)
    if not ent["allowed"]:
        return {"tier": "advanced", "allowed": False, "entitlement": ent}
    basic = basic_insights(db_path, account_id=account_id, since=since)
    # v0 "advanced" payload: basic + a simple per-meter share breakdown (trends/
    # forecasting are later work). The point here is the GATE is correct + honest.
    breakdown = basic["all_meters"]
    return {
        "tier": "advanced",
        "allowed": True,
        "entitlement": ent,
        "meters": basic["meters"],
        "breakdown": breakdown,
        "provider_cost_minor": basic["provider_cost_minor"],
        "currency": basic["currency"],
        "note": "Advanced Insights v0: breakdown only. Trends/budgets/forecasts/exports are later phases.",
    }
