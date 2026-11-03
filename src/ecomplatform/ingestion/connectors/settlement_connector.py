from __future__ import annotations

from ecomplatform.ingestion.base_connector import RestConnector


def _identity(raw: dict) -> dict:
    return dict(raw)


def _settlement(raw: dict) -> dict:
    out = dict(raw)
    # Missing optional fee components are treated as 0 so the settlement formula stays total.
    for f in ("seller_discount", "refund_amount", "commission_fee", "shipping_fee", "adjustment_amount"):
        if out.get(f) is None:
            out[f] = 0.0
    return out


class SettlementConnector(RestConnector):
    """Payments / refunds / settlements from the finance system."""
    normalizers = {"payments": _identity, "refunds": _identity, "settlements": _settlement}
