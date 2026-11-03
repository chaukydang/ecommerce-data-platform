"""Deterministic synthetic omnichannel data with *injected, labelled* discrepancies.

SIMULATED DATA - not a production integration. The ``manifest`` lists every intentionally injected
problem (rule_id + entity_id) so tests can prove the reconciliation rules find exactly those.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ecomplatform.util import to_iso

CHANNELS = ["shopify_sim", "tiktokshop_sim"]
PAID_STATUSES = ("PAID", "SHIPPED", "COMPLETED")
OMS_FROM_MP = {"UNPAID": "RECEIVED", "PAID": "ALLOCATED", "SHIPPED": "SHIPPED",
               "COMPLETED": "DELIVERED", "CANCELLED": "CANCELLED"}
SETTLEMENT_DELAY_DAYS = 7
COMMISSION_RATE = 0.05

# how many orders each injection touches
INJECTIONS = {
    "ORD_MISSING_IN_OMS": 3, "ORD_AMOUNT_MISMATCH": 3, "OMS_DUPLICATE_ORDER": 2,
    "PAY_MISSING": 3, "PAY_AMOUNT_MISMATCH": 3, "PAY_DUPLICATE_REF": 2, "PAY_CURRENCY_MISMATCH": 2,
    "REF_WITHOUT_PAYMENT": 2, "REF_EXCEEDS_PAID": 2, "REF_DUPLICATE_REF": 2,
    "SETTLE_FORMULA_MISMATCH": 3, "SETTLE_MISSING": 3,
}


@dataclass
class SimDataset:
    as_of: datetime
    marketplace_orders: list[dict] = field(default_factory=list)   # raw API shape (amounts in cents)
    oms_orders: list[dict] = field(default_factory=list)
    payments: list[dict] = field(default_factory=list)
    refunds: list[dict] = field(default_factory=list)
    settlements: list[dict] = field(default_factory=list)
    pos_transactions: list[dict] = field(default_factory=list)
    manifest: list[dict] = field(default_factory=list)   # {"rule_id","entity_id"}

    def injected(self) -> set[tuple[str, str]]:
        return {(m["rule_id"], m["entity_id"]) for m in self.manifest}

    def update_order_status(self, order_id: str, status: str, at: datetime) -> None:
        """Simulate a live status change at the source (bumps updated_at)."""
        for o in self.marketplace_orders:
            if o["order_id"] == order_id:
                o["status"], o["updated_at"] = status, to_iso(at)
                return
        raise KeyError(order_id)

    def add_order(self, order_id: str, total_cents: int, at: datetime, status: str = "UNPAID",
                  channel: str = CHANNELS[0]) -> None:
        self.marketplace_orders.append(_mp_order(order_id, channel, status, total_cents, f"C-{order_id}", at, at))


def _mp_order(order_id, channel, status, cents, customer, created, updated) -> dict:
    return {"order_id": order_id, "channel": channel, "status": status, "total_amount_minor": cents,
            "currency_exponent": 2, "currency": "USD", "customer_id": customer,
            "created_at": to_iso(created), "updated_at": to_iso(updated)}


def generate(n_orders: int = 300, seed: int = 7, as_of: datetime | None = None, inject: bool = True) -> SimDataset:
    rnd = random.Random(seed)
    as_of = as_of or datetime(2026, 11, 3, 9, 0, 0)
    ds = SimDataset(as_of=as_of)

    statuses = ["COMPLETED"] * 55 + ["SHIPPED"] * 15 + ["PAID"] * 15 + ["UNPAID"] * 5 + ["CANCELLED"] * 10
    settled: dict[str, dict] = {}
    refunds_by_order: dict[str, float] = {}

    # ---- base, internally consistent data -------------------------------------------------
    for i in range(1, n_orders + 1):
        oid = f"MP-{i:06d}"
        created = as_of - timedelta(days=rnd.uniform(0.2, 30))
        status = rnd.choice(statuses)
        updated = created + timedelta(hours=rnd.uniform(1, 72)) if status != "UNPAID" else created
        updated = min(updated, as_of - timedelta(minutes=5))
        cents = rnd.randint(1500, 50000)
        ds.marketplace_orders.append(_mp_order(oid, rnd.choice(CHANNELS), status, cents, f"C-{rnd.randint(1, 120):04d}", created, updated))
        amt = round(cents / 100, 2)
        ds.oms_orders.append({"oms_order_id": f"OMS-{i:06d}", "marketplace_order_id": oid,
                              "fulfillment_status": OMS_FROM_MP[status], "total_amount": amt, "currency": "USD",
                              "created_at": to_iso(created), "updated_at": to_iso(updated)})
        if status in PAID_STATUSES:
            paid_at = created + timedelta(minutes=5)
            ds.payments.append({"payment_id": f"PAYID-{i:06d}", "order_id": oid, "payment_ref": f"PAY-{oid}",
                                "amount": amt, "currency": "USD", "paid_at": to_iso(paid_at), "updated_at": to_iso(paid_at)})
        if status == "COMPLETED":
            refund = 0.0
            if rnd.random() < 0.08:
                refund = round(amt * 0.3, 2)
                r_at = updated + timedelta(hours=2)
                ds.refunds.append({"refund_id": f"REFID-{i:06d}", "order_id": oid, "refund_ref": f"REF-{oid}",
                                   "amount": refund, "currency": "USD", "status": "COMPLETED",
                                   "refunded_at": to_iso(r_at), "updated_at": to_iso(r_at)})
                refunds_by_order[oid] = refund
            if as_of - updated > timedelta(days=SETTLEMENT_DELAY_DAYS):
                disc = round(amt * 0.05, 2) if rnd.random() < 0.3 else 0.0
                comm = round(amt * COMMISSION_RATE, 2)
                ship = round(rnd.uniform(1.5, 3.0), 2)
                net = round(amt - disc - refund - comm - ship, 2)
                s_at = updated + timedelta(days=SETTLEMENT_DELAY_DAYS)
                s_at = min(s_at, as_of - timedelta(minutes=10))
                settled[oid] = {"settlement_id": f"SET-{i:06d}", "order_id": oid, "channel": ds.marketplace_orders[-1]["channel"],
                                "gross_amount": amt, "seller_discount": disc, "refund_amount": refund,
                                "commission_fee": comm, "shipping_fee": ship, "adjustment_amount": 0.0,
                                "net_payout": net, "currency": "USD", "settled_at": to_iso(s_at), "updated_at": to_iso(s_at)}
    ds.settlements = list(settled.values())

    # naturally-occurring *timing* cases: brand new orders not yet synced anywhere (< grace window)
    for k in range(3):
        at = as_of - timedelta(minutes=20 + 10 * k)
        oid = f"MP-FRESH{k + 1:02d}"
        ds.marketplace_orders.append(_mp_order(oid, CHANNELS[0], "PAID", 4200 + k, f"C-F{k}", at, at))

    # a few POS transactions (kept out of reconciliation; used for the multi-source demo)
    for j in range(1, 41):
        at = as_of - timedelta(hours=rnd.uniform(1, 400))
        ds.pos_transactions.append({"txn_id": f"POS-{j:05d}", "store_id": f"S{rnd.randint(1, 4):02d}",
                                    "type": "RETURN" if rnd.random() < 0.1 else "SALE",
                                    "amount": round(rnd.uniform(5, 300), 2), "currency": "USD",
                                    "occurred_at": to_iso(at), "updated_at": to_iso(at)})
    if inject:
        _inject(ds, rnd)
    return ds


def _inject(ds: SimDataset, rnd: random.Random) -> None:
    grace_cut = ds.as_of - timedelta(hours=2)
    settle_cut = ds.as_of - timedelta(days=SETTLEMENT_DELAY_DAYS + 3)
    mp = {o["order_id"]: o for o in ds.marketplace_orders}
    pay_by_order = {p["order_id"]: p for p in ds.payments}
    set_by_order = {s["order_id"]: s for s in ds.settlements}
    used: set[str] = set()

    def pick(n, pred):
        pool = [oid for oid, o in mp.items() if oid not in used and pred(oid, o)]
        rnd.shuffle(pool)
        chosen = sorted(pool[:n])
        assert len(chosen) == n, f"not enough orders to inject (need {n}, have {len(chosen)}) - increase n_orders"
        used.update(chosen)
        return chosen

    old = lambda o: o["created_at"] < to_iso(grace_cut)                     # noqa: E731
    paid = lambda oid, o: o["status"] in PAID_STATUSES and old(o) and oid in pay_by_order   # noqa: E731
    mark = lambda rule, eid: ds.manifest.append({"rule_id": rule, "entity_id": eid})         # noqa: E731

    # --- order level (marketplace vs OMS)
    for oid in pick(INJECTIONS["ORD_MISSING_IN_OMS"], lambda i, o: o["status"] != "UNPAID" and old(o) and o["status"] == "CANCELLED"):
        ds.oms_orders = [x for x in ds.oms_orders if x["marketplace_order_id"] != oid]
        mark("ORD_MISSING_IN_OMS", oid)
    oms = {x["marketplace_order_id"]: x for x in ds.oms_orders}
    for oid in pick(INJECTIONS["ORD_AMOUNT_MISMATCH"], lambda i, o: o["status"] == "CANCELLED" and i in oms):
        oms[oid]["total_amount"] = round(oms[oid]["total_amount"] + 5.0, 2)
        mark("ORD_AMOUNT_MISMATCH", oid)
    for oid in pick(INJECTIONS["OMS_DUPLICATE_ORDER"], lambda i, o: o["status"] == "CANCELLED" and i in oms):
        dup = dict(oms[oid]); dup["oms_order_id"] = "OMS-DUP-" + oid[3:]
        ds.oms_orders.append(dup)
        mark("OMS_DUPLICATE_ORDER", oid)

    # --- payments (use SHIPPED/PAID orders so settlement/refund tables stay untouched)
    shipped_or_paid = lambda oid, o: paid(oid, o) and o["status"] in ("SHIPPED", "PAID")   # noqa: E731
    for oid in pick(INJECTIONS["PAY_MISSING"], shipped_or_paid):
        ds.payments = [p for p in ds.payments if p["order_id"] != oid]
        mark("PAY_MISSING", oid)
    for oid in pick(INJECTIONS["PAY_AMOUNT_MISMATCH"], shipped_or_paid):
        pay_by_order[oid]["amount"] = round(pay_by_order[oid]["amount"] * 0.6, 2)
        mark("PAY_AMOUNT_MISMATCH", oid)
    for oid in pick(INJECTIONS["PAY_DUPLICATE_REF"], shipped_or_paid):
        dup = dict(pay_by_order[oid]); dup["payment_id"] = "PAYID-DUP-" + oid[3:]
        ds.payments.append(dup)
        mark("PAY_DUPLICATE_REF", pay_by_order[oid]["payment_ref"])
    for oid in pick(INJECTIONS["PAY_CURRENCY_MISMATCH"], shipped_or_paid):
        pay_by_order[oid]["currency"] = "EUR"
        mark("PAY_CURRENCY_MISMATCH", oid)

    # --- refunds
    unpaid_old = lambda oid, o: o["status"] in ("UNPAID", "CANCELLED") and old(o) and oid in oms   # noqa: E731
    for oid in pick(INJECTIONS["REF_WITHOUT_PAYMENT"], unpaid_old):
        at = ds.as_of - timedelta(hours=5)
        ds.refunds.append({"refund_id": f"REFID-X-{oid[3:]}", "order_id": oid, "refund_ref": f"REF-{oid}", "amount": 10.0,
                           "currency": "USD", "status": "COMPLETED", "refunded_at": to_iso(at), "updated_at": to_iso(at)})
        mark("REF_WITHOUT_PAYMENT", oid)
    recent_completed = lambda oid, o: (o["status"] == "COMPLETED" and old(o) and oid in pay_by_order   # noqa: E731
                                        and o["updated_at"] > to_iso(ds.as_of - timedelta(days=SETTLEMENT_DELAY_DAYS))
                                        and not any(r["order_id"] == oid for r in ds.refunds))
    for oid in pick(INJECTIONS["REF_EXCEEDS_PAID"], recent_completed):
        at = ds.as_of - timedelta(hours=6)
        ds.refunds.append({"refund_id": f"REFID-E-{oid[3:]}", "order_id": oid, "refund_ref": f"REF-{oid}",
                           "amount": round(pay_by_order[oid]["amount"] * 1.5, 2), "currency": "USD", "status": "COMPLETED",
                           "refunded_at": to_iso(at), "updated_at": to_iso(at)})
        mark("REF_EXCEEDS_PAID", oid)
    for oid in pick(INJECTIONS["REF_DUPLICATE_REF"], recent_completed):
        at = ds.as_of - timedelta(hours=6)
        base = {"order_id": oid, "refund_ref": f"REF-{oid}", "amount": 5.0, "currency": "USD", "status": "COMPLETED",
                "refunded_at": to_iso(at), "updated_at": to_iso(at)}
        ds.refunds.append({"refund_id": f"REFID-D1-{oid[3:]}", **base})
        ds.refunds.append({"refund_id": f"REFID-D2-{oid[3:]}", **base})
        mark("REF_DUPLICATE_REF", f"REF-{oid}")

    # --- settlements
    settled_old = lambda oid, o: oid in set_by_order and o["updated_at"] < to_iso(settle_cut)   # noqa: E731
    for oid in pick(INJECTIONS["SETTLE_FORMULA_MISMATCH"], settled_old):
        set_by_order[oid]["net_payout"] = round(set_by_order[oid]["net_payout"] - 10.0, 2)
        mark("SETTLE_FORMULA_MISMATCH", oid)
    for oid in pick(INJECTIONS["SETTLE_MISSING"], settled_old):
        ds.settlements = [s for s in ds.settlements if s["order_id"] != oid]
        mark("SETTLE_MISSING", oid)
