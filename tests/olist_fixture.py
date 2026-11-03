"""Writes a small CSV set with the exact column layout of the Kaggle Olist files (SYNTHETIC rows, for tests only)."""
from __future__ import annotations

import random
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd


def write_olist_fixture(folder: Path, n: int = 240, seed: int = 5) -> dict:
    rnd = random.Random(seed)
    base = datetime(2017, 6, 1, 10, 0, 0)
    orders, items, pays = [], [], []
    manifest: dict[str, set] = {"PAY_MISSING": set(), "PAY_AMOUNT_MISMATCH": set()}
    for i in range(n):
        oid = f"ord{i:05d}"
        t0 = base + timedelta(days=rnd.uniform(0, 400))
        status = rnd.choices(["delivered", "shipped", "canceled", "approved", "created", "unavailable"], [70, 8, 6, 6, 5, 5])[0]
        approved = t0 + timedelta(hours=1) if status != "created" else None
        carrier = t0 + timedelta(days=2) if status in ("delivered", "shipped") else None
        delivered = t0 + timedelta(days=7) if status == "delivered" else None
        orders.append({"order_id": oid, "customer_id": f"cust{rnd.randint(1, 80):04d}", "order_status": status,
                       "order_purchase_timestamp": t0, "order_approved_at": approved, "order_delivered_carrier_date": carrier,
                       "order_delivered_customer_date": delivered, "order_estimated_delivery_date": t0 + timedelta(days=15)})
        total = 0.0
        if status not in ("canceled", "unavailable"):
            for k in range(1, rnd.randint(1, 3) + 1):
                price, freight = round(rnd.uniform(20, 300), 2), round(rnd.uniform(5, 40), 2)
                total += price + freight
                items.append({"order_id": oid, "order_item_id": k, "product_id": f"p{rnd.randint(1, 50)}", "seller_id": f"s{rnd.randint(1, 9)}",
                              "shipping_limit_date": t0 + timedelta(days=3), "price": price, "freight_value": freight})
        total = round(total, 2)
        if status in ("delivered", "shipped", "approved"):
            if rnd.random() < 0.3 and total > 50:                                   # split payment: voucher + card (real-life pattern)
                v = round(total * 0.3, 2)
                pays += [{"order_id": oid, "payment_sequential": 1, "payment_type": "voucher", "payment_installments": 1, "payment_value": v},
                         {"order_id": oid, "payment_sequential": 2, "payment_type": "credit_card", "payment_installments": 3,
                          "payment_value": round(total - v, 2)}]
            else:
                pays.append({"order_id": oid, "payment_sequential": 1, "payment_type": "boleto", "payment_installments": 1, "payment_value": total})
    # planted problems on delivered orders
    delivered_ids = [o["order_id"] for o in orders if o["order_status"] == "delivered"]
    rnd.shuffle(delivered_ids)
    for oid in delivered_ids[:3]:                                                   # delivered but never paid
        pays[:] = [p for p in pays if p["order_id"] != oid]
        manifest["PAY_MISSING"].add(oid)
    for oid in delivered_ids[3:6]:                                                  # customer paid only 60 %
        for p in pays:
            if p["order_id"] == oid:
                p["payment_value"] = round(p["payment_value"] * 0.6, 2)
        manifest["PAY_AMOUNT_MISMATCH"].add(oid)
    folder.mkdir(parents=True, exist_ok=True)
    fmt = "%Y-%m-%d %H:%M:%S"
    pd.DataFrame(orders).to_csv(folder / "olist_orders_dataset.csv", index=False, date_format=fmt)
    pd.DataFrame(items).to_csv(folder / "olist_order_items_dataset.csv", index=False, date_format=fmt)
    pd.DataFrame(pays).to_csv(folder / "olist_order_payments_dataset.csv", index=False)
    return {"n_orders": n, "n_payments": len(pays), "manifest": manifest, "statuses": {o["order_id"]: o["order_status"] for o in orders}}
