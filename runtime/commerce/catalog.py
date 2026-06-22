"""
runtime/commerce/catalog.py — product / plan / price / feature catalogue (ADR-0003).

The single source of truth for the commercial catalogue. Pure stdlib (json) so it
loads with zero runtime dependencies (PyYAML is only a dev extra; the runtime is
stdlib-first). Studio AND the website render plans/prices from this catalogue so
they cannot drift (handover 02 §7).

Money is always (currency, amount_minor) — never floats. Prices are versioned and
immutable after use (do not edit an in-use price row; add a new version).

This module is DATA + read accessors only. It contains no plan-gating logic — that
is the entitlement resolver (runtime/commerce/entitlements.py, ADR-0004).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

_CATALOG_PATH = Path(__file__).resolve().parent / "catalog" / "catalog.json"


class CatalogError(ValueError):
    """Raised when the catalogue is missing or fails referential validation."""


def load_catalog(path: str | Path | None = None) -> dict[str, Any]:
    """Load and validate the catalogue. Raises CatalogError on integrity failure."""
    p = Path(path) if path else _CATALOG_PATH
    if not p.is_file():
        raise CatalogError(f"catalogue not found: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CatalogError(f"catalogue is not valid JSON: {exc}") from exc
    validate_catalog(data)
    return data


@lru_cache(maxsize=1)
def _default_catalog() -> dict[str, Any]:
    return load_catalog()


def validate_catalog(data: dict[str, Any]) -> None:
    """Cross-reference integrity: ids unique; every reference resolves; money sane."""
    for key in ("products", "plans", "prices", "features", "plan_features", "meters"):
        if not isinstance(data.get(key), list):
            raise CatalogError(f"catalogue missing list section: {key!r}")

    product_ids = {p["id"] for p in data["products"]}
    plan_ids = {p["id"] for p in data["plans"]}
    feature_ids = {f["id"] for f in data["features"]}

    if len(product_ids) != len(data["products"]):
        raise CatalogError("duplicate product id")
    if len(plan_ids) != len(data["plans"]):
        raise CatalogError("duplicate plan id")
    if len(feature_ids) != len(data["features"]):
        raise CatalogError("duplicate feature id")

    for pl in data["plans"]:
        if pl["product_id"] not in product_ids:
            raise CatalogError(f"plan {pl['id']!r} references unknown product {pl['product_id']!r}")

    for pr in data["prices"]:
        if pr["plan_id"] not in plan_ids:
            raise CatalogError(f"price {pr['id']!r} references unknown plan {pr['plan_id']!r}")
        cur = pr.get("currency")
        if not (isinstance(cur, str) and len(cur) == 3):
            raise CatalogError(f"price {pr['id']!r} has invalid currency {cur!r}")
        amt = pr.get("amount_minor")
        if not isinstance(amt, int) or isinstance(amt, bool) or amt < 0:
            raise CatalogError(f"price {pr['id']!r} amount_minor must be a non-negative int (minor units), got {amt!r}")

    seen_pf = set()
    for pf in data["plan_features"]:
        if pf["plan_id"] not in plan_ids:
            raise CatalogError(f"plan_feature references unknown plan {pf['plan_id']!r}")
        if pf["feature_id"] not in feature_ids:
            raise CatalogError(f"plan_feature references unknown feature {pf['feature_id']!r}")
        key = (pf["plan_id"], pf["feature_id"])
        if key in seen_pf:
            raise CatalogError(f"duplicate plan_feature mapping {key}")
        seen_pf.add(key)


# ── read accessors ────────────────────────────────────────────────────────────
def list_plans(catalog: dict | None = None, *, public_only: bool = False) -> list[dict]:
    cat = catalog or _default_catalog()
    plans = sorted(cat["plans"], key=lambda p: p.get("rank", 0))
    return [p for p in plans if (not public_only or p.get("public"))]


def get_plan(plan_id: str, catalog: dict | None = None) -> Optional[dict]:
    cat = catalog or _default_catalog()
    return next((p for p in cat["plans"] if p["id"] == plan_id), None)


def get_feature(feature_id: str, catalog: dict | None = None) -> Optional[dict]:
    cat = catalog or _default_catalog()
    return next((f for f in cat["features"] if f["id"] == feature_id), None)


def features_for_plan(plan_id: str, catalog: dict | None = None) -> set[str]:
    cat = catalog or _default_catalog()
    return {pf["feature_id"] for pf in cat["plan_features"] if pf["plan_id"] == plan_id}


def plans_with_feature(feature_id: str, catalog: dict | None = None) -> list[str]:
    """Plan ids that include a feature, cheapest-rank first (for upgrade_target)."""
    cat = catalog or _default_catalog()
    have = {pf["plan_id"] for pf in cat["plan_features"] if pf["feature_id"] == feature_id}
    return [p["id"] for p in list_plans(cat) if p["id"] in have]


def prices_for_plan(plan_id: str, catalog: dict | None = None) -> list[dict]:
    cat = catalog or _default_catalog()
    return [pr for pr in cat["prices"] if pr["plan_id"] == plan_id and pr.get("active", True)]


def list_meters(catalog: dict | None = None) -> list[dict]:
    cat = catalog or _default_catalog()
    return list(cat["meters"])


def get_meter(meter_id: str, catalog: dict | None = None) -> Optional[dict]:
    cat = catalog or _default_catalog()
    return next((m for m in cat["meters"] if m["id"] == meter_id), None)
