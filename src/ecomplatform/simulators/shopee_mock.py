"""A local MOCK of the Shopee Open Platform v2 API *shape* (HMAC-signed calls, 4 h access tokens with rotating refresh tokens,
errors inside HTTP 200 bodies, 15-day order windows, escrow detail).

For tests and offline development of the Shopee connector only - it is NOT Shopee and its data is synthetic.
Real data: Shopee sandbox / your shop via docs/shopee_quickstart.md.
"""
from __future__ import annotations

import hashlib
import hmac
import random
import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Request

from .server import Faults

UTC = timezone.utc

PARTNER_ID, PARTNER_KEY, SHOP_ID = "1001", "mock-partner-key-0123456789", "555"
PAID = ("READY_TO_SHIP", "SHIPPED", "COMPLETED")


@dataclass
class ShopeeData:
    orders: dict[str, dict] = field(default_factory=dict)       # order_sn -> order detail (epoch times)
    escrow: dict[str, dict] = field(default_factory=dict)       # order_sn -> order_income
    manifest: list[dict] = field(default_factory=list)


def _escrow(total: float, rnd: random.Random) -> dict:
    inc = {"buyer_total_amount": total, "original_price": total, "commission_fee": round(total * 0.05, 2),
           "service_fee": round(total * 0.02, 2), "seller_transaction_fee": 2.0, "buyer_transaction_fee": round(rnd.uniform(0, 1.5), 2),
           "shopee_discount": round(rnd.choice([0, 0, 3.0, 5.0]), 2), "voucher_from_shopee": 0.0, "coins": 0.0, "payment_promotion": 0.0,
           "cross_border_tax": 0.0, "seller_coin_cash_back": 0.0, "escrow_tax": 0.0, "drc_adjustable_refund": 0.0,
           "final_shipping_fee": -round(rnd.uniform(1, 3), 2)}
    inc["escrow_amount"] = round(inc["buyer_total_amount"] + inc["shopee_discount"] + inc["voucher_from_shopee"] + inc["coins"]
                                 + inc["payment_promotion"] - inc["buyer_transaction_fee"] - inc["cross_border_tax"] - inc["commission_fee"]
                                 - inc["service_fee"] - inc["seller_transaction_fee"] - inc["seller_coin_cash_back"] - inc["escrow_tax"]
                                 - inc["drc_adjustable_refund"] + inc["final_shipping_fee"], 2)
    return inc


def generate_shopee(n_orders: int = 70, seed: int = 21, as_of: datetime | None = None) -> ShopeeData:
    rnd = random.Random(seed)
    as_of = (as_of or datetime(2026, 11, 3, 9, 0, 0)).replace(tzinfo=UTC)
    d = ShopeeData()
    for i in range(n_orders):
        sn = f"2611{i:07d}ABC"
        created = as_of - timedelta(days=rnd.uniform(0.3, 40))                     # > 15 days: forces several time windows
        total = round(rnd.uniform(50_000, 900_000), 0)                             # VND-like amounts
        status = rnd.choices(["COMPLETED", "SHIPPED", "READY_TO_SHIP", "UNPAID", "CANCELLED"], [50, 15, 15, 10, 10])[0]
        upd = min(created + timedelta(days=7 if status == "COMPLETED" else 0.2), as_of - timedelta(minutes=5))
        o = {"order_sn": sn, "order_status": status, "region": "VN", "currency": "VND", "total_amount": total,
             "create_time": int(created.timestamp()), "update_time": int(upd.timestamp()), "buyer_user_id": 9000 + rnd.randint(1, 40)}
        if status in PAID:
            o["pay_time"] = int((created + timedelta(minutes=3)).timestamp())
        d.orders[sn] = o
        if status == "COMPLETED":
            d.escrow[sn] = _escrow(total, rnd)

    done = [o for o in d.orders.values() if o["order_status"] == "COMPLETED"
            and o["update_time"] < int((as_of - timedelta(days=10)).timestamp())]
    rnd.shuffle(done)
    for o in done[:2]:                                                             # payout does not add up
        d.escrow[o["order_sn"]]["escrow_amount"] -= 8_000.0
        d.manifest.append({"rule_id": "SETTLE_FORMULA_MISMATCH", "entity_id": f"SPE-{o['order_sn']}"})
    g = done[2]                                                                    # gross differs from the order total
    inc = _escrow(g["total_amount"] - 20_000, random.Random(1))
    d.escrow[g["order_sn"]] = inc
    d.manifest.append({"rule_id": "SETTLE_GROSS_MISMATCH", "entity_id": f"SPE-{g['order_sn']}"})
    m = done[3]                                                                    # completed long ago, no escrow
    del d.escrow[m["order_sn"]]
    d.manifest.append({"rule_id": "SETTLE_MISSING", "entity_id": f"SPE-{m['order_sn']}"})
    paid = [o for o in d.orders.values() if o["order_status"] in ("READY_TO_SHIP", "SHIPPED")
            and o["create_time"] < int((as_of - timedelta(days=1)).timestamp())]
    p = paid[0]
    del p["pay_time"]                                                              # paid status but no payment record
    d.manifest.append({"rule_id": "PAY_MISSING", "entity_id": f"SPE-{p['order_sn']}"})
    return d


def create_shopee_mock(data: ShopeeData, faults: Faults | None = None) -> FastAPI:
    app = FastAPI(title="Shopee Open Platform v2 MOCK (synthetic data)")
    app.state.data, app.state.faults = data, faults or Faults()
    state = {"refresh": "rt_" + secrets.token_hex(8), "tokens": set()}
    app.state.shopee = state
    app.state.window_calls = 0
    lock = threading.Lock()

    def err(code: str, msg: str = ""):
        return {"error": code, "message": msg, "request_id": secrets.token_hex(6)}

    def sign_ok(request: Request, shop_api: bool) -> bool:
        q = request.query_params
        base = f"{PARTNER_ID}{request.url.path}{q.get('timestamp')}" + (f"{q.get('access_token')}{SHOP_ID}" if shop_api else "")
        expect = hmac.new(PARTNER_KEY.encode(), base.encode(), hashlib.sha256).hexdigest()
        return q.get("partner_id") == PARTNER_ID and hmac.compare_digest(expect, q.get("sign", ""))

    def guard(request: Request):
        """Common checks for shop APIs: fault injection, signature, token."""
        f: Faults = app.state.faults
        with lock:
            f.calls += 1
            n = f.calls
        if n in f.rate_limit_on:
            return err("error_too_many_request", "rate limit")
        if n in f.server_error_on or (f.fail_from_call is not None and n >= f.fail_from_call):
            return err("error_server", "internal")
        if not sign_ok(request, True):
            return err("error_sign", "Wrong sign")
        if request.query_params.get("access_token") not in state["tokens"]:
            return err("error_auth", "Invalid access_token")
        return None

    @app.post("/api/v2/auth/access_token/get")
    async def refresh(request: Request):
        if not sign_ok(request, False):
            return err("error_sign", "Wrong sign")
        body = await request.json()
        if body.get("refresh_token") != state["refresh"] or str(body.get("shop_id")) != SHOP_ID:
            return err("error_auth", "Invalid refresh_token")
        state["refresh"] = "rt_" + secrets.token_hex(8)                              # refresh tokens rotate
        tok = "at_" + secrets.token_hex(10)
        state["tokens"].add(tok)
        return {"error": "", "message": "", "access_token": tok, "refresh_token": state["refresh"], "expire_in": 14400,
                "partner_id": int(PARTNER_ID), "shop_id": int(SHOP_ID), "request_id": secrets.token_hex(6)}

    @app.get("/api/v2/order/get_order_list")
    def order_list(request: Request):
        if (e := guard(request)) is not None:
            return e
        q = request.query_params
        t0, t1, size = int(q["time_from"]), int(q["time_to"]), int(q.get("page_size", 20))
        if t1 - t0 > 15 * 86400 or q.get("time_range_field") != "update_time":
            return err("error_param", "The time range is too long (max 15 days)")
        app.state.window_calls += 1
        rows = sorted((o for o in app.state.data.orders.values() if t0 <= o["update_time"] <= t1), key=lambda o: (o["update_time"], o["order_sn"]))
        off = int(q.get("cursor") or 0)
        page = rows[off:off + size]
        more = off + size < len(rows)
        return {"error": "", "message": "", "response": {"more": more, "next_cursor": str(off + size) if more else "",
                                                          "order_list": [{"order_sn": o["order_sn"], "order_status": o["order_status"]} for o in page]}}

    @app.get("/api/v2/order/get_order_detail")
    def order_detail(request: Request):
        if (e := guard(request)) is not None:
            return e
        q = request.query_params
        sns = q.get("order_sn_list", "").split(",")
        if len(sns) > 50:
            return err("error_param", "order_sn_list: at most 50")
        optional = set(q.get("response_optional_fields", "").split(","))
        out = []
        for sn in sns:
            o = app.state.data.orders.get(sn)
            if not o:
                continue
            row = {k: v for k, v in o.items() if k in ("order_sn", "order_status", "region", "currency", "create_time", "update_time")
                   or k in optional}                     # optional fields only when requested, as in the real API
            out.append(row)
        return {"error": "", "message": "", "response": {"order_list": out}}

    @app.get("/api/v2/payment/get_escrow_detail")
    def escrow(request: Request):
        if (e := guard(request)) is not None:
            return e
        sn = request.query_params.get("order_sn", "")
        inc = app.state.data.escrow.get(sn)
        if inc is None:
            return err("error_param", "escrow not available")
        return {"error": "", "message": "", "response": {"order_sn": sn, "order_income": inc}}

    @app.get("/health")
    def health():
        return {"status": "ok", "mock": True}

    return app
