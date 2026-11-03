"""Olist Brazilian E-Commerce (real, anonymised marketplace data, 2016-2018) from the Kaggle CSV files.

Download https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce , unzip, and point OLIST_DATA_DIR at the folder containing
olist_orders_dataset.csv, olist_order_items_dataset.csv and olist_order_payments_dataset.csv.

Mapping to the platform's canonical records
  orders   : total_amount = sum(price + freight_value) of the order items; status from order_status; created = purchase time;
             updated = latest known event time. Amounts are Brazilian reais (BRL). Timestamps are treated as UTC.
  payments : one record per payment_sequential (voucher / credit card / boleto ...), amount = payment_value.
Olist has no refunds, settlements or OMS data, so those checks are not part of this profile.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from ecomplatform.ingestion.base_connector import BaseConnector
from ecomplatform.ingestion.core.retry_policy import PermanentError, RetryPolicy
from ecomplatform.util import to_iso

STATUS = {"delivered": "COMPLETED", "shipped": "SHIPPED", "invoiced": "PAID", "processing": "PAID", "approved": "PAID",
          "created": "PENDING", "canceled": "CANCELLED", "unavailable": "CANCELLED"}
FILES = {"orders": "olist_orders_dataset.csv", "items": "olist_order_items_dataset.csv", "payments": "olist_order_payments_dataset.csv"}
TS_COLS = ["order_purchase_timestamp", "order_approved_at", "order_delivered_carrier_date", "order_delivered_customer_date"]
CHUNK = 5000


class _FileClient:
    """Stand-in for an API client so the ingestion runner can treat file sources uniformly."""

    def __init__(self, retry: RetryPolicy | None = None):
        self.retry = retry or RetryPolicy()


def _iso(series: pd.Series) -> pd.Series:
    return series.dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class OlistCsvConnector(BaseConnector):
    @classmethod
    def build_client(cls, cfg: dict[str, Any], retry: RetryPolicy | None = None) -> _FileClient:
        return _FileClient(retry)

    def normalize(self, raw: dict) -> dict:
        return dict(raw)                                   # records are already canonical

    def _read(self, key: str, **kw) -> pd.DataFrame:
        folder = os.getenv("OLIST_DATA_DIR")
        if not folder:
            raise PermanentError("OLIST_DATA_DIR is not set (folder with the Kaggle Olist CSV files, see docs/olist_quickstart.md)")
        path = Path(folder).expanduser() / FILES[key]
        if not path.exists():
            raise PermanentError(f"{path} not found - download the Olist dataset from Kaggle and unzip it into OLIST_DATA_DIR")
        return pd.read_csv(path, **kw)

    def _orders(self) -> pd.DataFrame:
        o = self._read("orders", parse_dates=TS_COLS)
        items = self._read("items")
        total = (items.assign(v=items["price"] + items["freight_value"]).groupby("order_id")["v"].sum().round(2))
        o["total_amount"] = o["order_id"].map(total).fillna(0.0)
        o["status"] = o["order_status"].map(STATUS).fillna("PENDING")
        o["updated"] = o[TS_COLS].max(axis=1)
        return o

    def fetch_pages(self, since: datetime, until: datetime) -> Iterator[list[dict]]:
        lo, hi = pd.Timestamp(since), pd.Timestamp(until)
        orders = self._orders()
        if self.entity == "orders":
            df = orders[(orders["updated"] >= lo) & (orders["updated"] <= hi)]
            out = pd.DataFrame({
                "order_id": df["order_id"], "channel": "olist", "status": df["status"], "total_amount": df["total_amount"],
                "currency": "BRL", "customer_id": df["customer_id"],
                "created_at": _iso(df["order_purchase_timestamp"]), "updated_at": _iso(df["updated"])})
        elif self.entity == "payments":
            p = self._read("payments")
            when = orders.set_index("order_id")
            ts = p["order_id"].map(when["order_approved_at"].fillna(when["order_purchase_timestamp"]))
            upd = p["order_id"].map(when["updated"])
            keep = ts.notna() & (upd >= lo) & (upd <= hi)
            p, ts, upd = p[keep], ts[keep], upd[keep]
            pid = p["order_id"] + "-" + p["payment_sequential"].astype(str)
            out = pd.DataFrame({"payment_id": "OLP-" + pid, "order_id": p["order_id"], "payment_ref": "OLP-" + pid,
                                "amount": p["payment_value"].round(2), "currency": "BRL",
                                "paid_at": _iso(ts), "updated_at": _iso(upd)})
        else:
            raise PermanentError(f"unsupported Olist entity: {self.entity}")
        records = out.to_dict("records")
        for i in range(0, len(records), CHUNK):
            yield records[i:i + CHUNK]


__all__ = ["OlistCsvConnector", "to_iso"]
