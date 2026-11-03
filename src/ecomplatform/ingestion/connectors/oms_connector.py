from __future__ import annotations

from ecomplatform.ingestion.base_connector import RestConnector

OMS_STATUS_MAP = {
    "RECEIVED": "PENDING", "ALLOCATED": "PAID", "SHIPPED": "SHIPPED",
    "DELIVERED": "COMPLETED", "CANCELLED": "CANCELLED",
}


def _order(raw: dict) -> dict:
    return {
        "oms_order_id": raw.get("oms_order_id"),
        "order_id": raw.get("marketplace_order_id"),
        "status": OMS_STATUS_MAP.get(str(raw.get("fulfillment_status", "")).upper(), "UNKNOWN"),
        "total_amount": raw.get("total_amount"),
        "currency": raw.get("currency"),
        "created_at": raw.get("created_at"),
        "updated_at": raw.get("updated_at"),
    }


class OmsConnector(RestConnector):
    normalizers = {"orders": _order}
