from __future__ import annotations

from ecomplatform.ingestion.base_connector import RestConnector


def _txn(raw: dict) -> dict:
    return {
        "txn_id": raw.get("txn_id"),
        "store_id": raw.get("store_id"),
        "txn_type": str(raw.get("type", "")).upper(),
        "amount": raw.get("amount"),
        "currency": raw.get("currency"),
        "occurred_at": raw.get("occurred_at"),
        "updated_at": raw.get("updated_at"),
    }


class PosConnector(RestConnector):
    normalizers = {"transactions": _txn}
