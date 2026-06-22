"""
runtime/commerce/admin.py — internal admin READ-ONLY views (ADR-0008, handover 04 §12).

Read-only aggregates over the commerce store + catalogue for an internal admin
surface. NO mutation here — any admin action (grant credits, override entitlement,
refund, etc.) must go through the StudioService approval/audit path and is out of
the smallest-safe scope. RBAC roles (support_read/billing_ops/.../super_admin) are
defined in ADR-0012 and enforced at the surface, not in this read layer.
"""

from __future__ import annotations

from typing import Any

from runtime.commerce import catalog as _catalog
from runtime.commerce import store


def overview(db_path) -> dict[str, Any]:
    """Read-only operational snapshot for the admin dashboard."""
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        accounts = conn.execute("SELECT COUNT(*) AS n FROM accounts").fetchone()["n"]
        by_plan = {
            r["plan_id"]: r["n"]
            for r in conn.execute("SELECT plan_id, COUNT(*) AS n FROM accounts GROUP BY plan_id")
        }
        usage_events = conn.execute("SELECT COUNT(*) AS n FROM usage_events").fetchone()["n"]
        audit_events = conn.execute("SELECT COUNT(*) AS n FROM audit_events").fetchone()["n"]

        def _count(tbl: str, where: str = "") -> int:
            return conn.execute(f"SELECT COUNT(*) AS n FROM {tbl}{where}").fetchone()["n"]

        domain_events = _count("domain_events")
        subscriptions = _count("subscriptions")
        active_grants = _count("entitlement_grants", " WHERE revoked_at IS NULL")
        roles = _count("roles")
        aggregates = _count("usage_aggregates")
    finally:
        conn.close()

    return {
        "schema_version": store.get_schema_version(db_path),
        "accounts_total": accounts,
        "accounts_by_plan": by_plan,
        "usage_events_total": usage_events,
        "usage_aggregates_total": aggregates,
        "audit_events_total": audit_events,
        "domain_events_total": domain_events,
        "subscriptions_total": subscriptions,
        "active_grants_total": active_grants,
        "roles_total": roles,
        "provider_cost_minor_gbp": store.provider_cost_total(db_path, currency="GBP"),
        "catalogue_plans": [p["id"] for p in _catalog.list_plans()],
        "billing_status": "test_mode_unconfigured",
        "note": "Read-only. Live billing/admin mutation is out of smallest-safe scope (Phase E).",
    }
