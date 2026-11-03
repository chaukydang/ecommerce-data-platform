from __future__ import annotations

from ecomplatform.ingestion.base_connector import RestConnector

# Marketplace status vocabulary -> canonical platform vocabulary
STATUS_MAP = {
    "UNPAID": "PENDING", "PAID": "PAID", "SHIPPED": "SHIPPED",
    "COMPLETED": "COMPLETED", "CANCELLED": "CANCELLED", "CANCELED": "CANCELLED",
}


def _order(raw: dict) -> dict:
    # Marketplace sends money in minor units (cents); the platform stores major units.
    exp = raw.get("currency_exponent", 2)
    return {
        "order_id": raw.get("order_id"),
        "channel": raw.get("channel"),
        "status": STATUS_MAP.get(str(raw.get("status", "")).upper(), "UNKNOWN"),
        "total_amount": None if raw.get("total_amount_minor") is None else round(raw["total_amount_minor"] / 10 ** exp, 2),
        "currency": raw.get("currency"),
        "customer_id": raw.get("customer_id"),
        "created_at": raw.get("created_at"),
        "updated_at": raw.get("updated_at"),
    }


class MarketplaceConnector(RestConnector):
    normalizers = {"orders": _order}
