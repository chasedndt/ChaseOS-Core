"""
runtime/commerce/ — ChaseOS commercial foundation (Phase C, smallest-safe slice).

Local/test-mode, stdlib-first commercial substrate per the Commercial Readiness
ADRs (docs/commercial-readiness/adr/). NO real billing, NO Stripe keys, NO network,
NO live auth, NO money movement. The double-entry ledger + balances + Stripe are
deferred to Phase E behind their launch gates.

Modules:
  catalog       product/plan/price/feature catalogue (ADR-0003)
  entitlements  default-deny entitlement resolver + account-scoped check (ADR-0004)
  flags         feature-flag resolver (ADR-0004)
  store         SQLite store v2: accounts/workspaces/usage/cost/audit + grants/
                prices/aggregates/events/roles/subs (ADR-0005/0007/0012)
  grants        persisted entitlement grants (ADR-0004)
  events        canonical domain events (handover 12 §8)
  pricing       immutable price versions + retail/margin calc (ADR-0005)
  aggregation   usage rollups → usage_aggregates (ADR-0005)
  insights      free basic + entitlement-gated advanced Insights (ADR-0004/0005)
  admin         read-only admin views (ADR-0008)
"""

from runtime.commerce import (  # noqa: F401
    accounts,
    admin,
    aggregation,
    catalog,
    entitlements,
    events,
    flags,
    grants,
    insights,
    pricing,
    store,
)

__all__ = [
    "accounts", "admin", "aggregation", "catalog", "entitlements", "events",
    "flags", "grants", "insights", "pricing", "store",
]
