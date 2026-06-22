"""
runtime/commerce/pricing.py — price versions + retail/margin calc (ADR-0005, handover 04 §8/§9).

Per-meter retail rate cards as IMMUTABLE price versions: a new rate creates a new
version and retires the prior active one — existing versions are never mutated, so
historical charges are never recomputed at current rates. Money is minor units;
rates and quantities are decimal-exact (Decimal); rounding to integer minor units
happens only at the total.

`unit_amount_minor` is stored as a decimal STRING in minor units PER meter-unit
(e.g. "0.0004" = 0.0004 pence per token). No floats anywhere.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

from runtime.commerce import store


def set_price(
    db_path,
    *,
    meter_id: str,
    currency: str,
    unit_amount_minor: str,
    orchestration_fee_minor: int = 0,
    effective_from: Optional[str] = None,
) -> dict[str, Any]:
    """Create a new immutable price version for a meter, retiring the prior active one."""
    Decimal(unit_amount_minor)  # validate
    if not (isinstance(currency, str) and len(currency) == 3):
        raise ValueError("currency must be a 3-letter code")
    if not isinstance(orchestration_fee_minor, int) or isinstance(orchestration_fee_minor, bool):
        raise ValueError("orchestration_fee_minor must be an int (minor units)")
    pid = store.new_id("pv")
    conn = store.connect(db_path)
    try:
        row = conn.execute(
            "SELECT COALESCE(MAX(version),0) AS v FROM price_versions WHERE meter_id=? AND currency=?",
            (meter_id, currency),
        ).fetchone()
        version = int(row["v"]) + 1
        conn.execute(
            "UPDATE price_versions SET retired_at=? WHERE meter_id=? AND currency=? AND retired_at IS NULL",
            (store.now_iso(), meter_id, currency),
        )
        conn.execute(
            """INSERT INTO price_versions
               (price_version_id,meter_id,currency,unit_amount_minor,version,effective_from,retired_at,orchestration_fee_minor)
               VALUES (?,?,?,?,?,?,?,?)""",
            (pid, meter_id, currency, str(unit_amount_minor), version,
             effective_from or store.now_iso(), None, orchestration_fee_minor),
        )
        conn.commit()
    finally:
        conn.close()
    return {"price_version_id": pid, "meter_id": meter_id, "currency": currency, "version": version}


def active_price(db_path, meter_id: str, currency: str = "GBP") -> Optional[dict]:
    conn = store.connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM price_versions WHERE meter_id=? AND currency=? AND retired_at IS NULL "
            "ORDER BY version DESC LIMIT 1",
            (meter_id, currency),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_price_versions(db_path, meter_id: str) -> list[dict]:
    conn = store.connect(db_path)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM price_versions WHERE meter_id=? ORDER BY currency, version", (meter_id,)
        ).fetchall()]
    finally:
        conn.close()


def compute_retail(
    db_path,
    *,
    meter_id: str,
    quantity: str | int | float | Decimal,
    currency: str = "GBP",
    tax_rate: str = "0",
) -> dict[str, Any]:
    """retail = quantity × unit_amount + orchestration fee + tax. Decimal-exact.

    Returns exact decimal strings + rounded integer minor-unit totals. Raises if no
    active price version exists for the meter (never silently charges zero).
    """
    price = active_price(db_path, meter_id, currency)
    if price is None:
        raise ValueError(f"no active price version for meter {meter_id!r} in {currency}")
    qty = Decimal(str(quantity))
    rate = Decimal(price["unit_amount_minor"])
    fee = Decimal(price["orchestration_fee_minor"])
    base_exact = qty * rate                       # minor units, exact
    subtotal_exact = base_exact + fee
    tax_exact = subtotal_exact * Decimal(tax_rate)
    total_exact = subtotal_exact + tax_exact

    def _round(d: Decimal) -> int:
        return int(d.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    return {
        "meter_id": meter_id,
        "currency": currency,
        "price_version": price["version"],
        "quantity": str(qty),
        "base_exact_minor": str(base_exact),
        "orchestration_fee_minor": int(fee),
        "tax_rate": str(Decimal(tax_rate)),
        "tax_exact_minor": str(tax_exact),
        "total_exact_minor": str(total_exact),
        "total_minor": _round(total_exact),
    }


def gross_margin(retail_total_minor: int, provider_cost_minor: int) -> dict[str, Any]:
    """Gross margin in minor units + percentage (None when retail is 0)."""
    margin = int(retail_total_minor) - int(provider_cost_minor)
    pct = None
    if retail_total_minor:
        pct = float(Decimal(margin) / Decimal(retail_total_minor) * 100)
    return {"gross_margin_minor": margin, "gross_margin_pct": pct}
