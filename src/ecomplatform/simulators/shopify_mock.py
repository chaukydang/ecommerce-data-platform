"""A local MOCK of the Shopify Admin REST API *shape* (orders, transactions, Link pagination, 429s, client-credentials token).

For tests and offline development of the Shopify connector only - it is NOT Shopify and its data is synthetic.
Real data: point SHOPIFY_STORE_URL at a development store (docs/shopify_quickstart.md).
"""
from __future__ import annotations

import base64
import random
import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from ecomplatform.util import parse_iso

from .server import Faults

TZ = timezone(timedelta(hours=7))           # Shopify returns store-local offsets, e.g. +07:00
CLIENT_ID, CLIENT_SECRET = "mock-client-id", "mock-client-secret"


def _iso(dt: datetime) -> str:
    return dt.astimezone(TZ).isoformat(timespec="seconds")


@dataclass
class ShopifyData:
    orders: list[dict] = field(default_factory=list)
    transactions: dict[int, list[dict]] = field(default_factory=dict)
    manifest: list[dict] = field(default_factory=list)       # injected problems: {"rule_id","entity_id"}


def generate_shopify(n_orders: int = 60, seed: int = 11, as_of: datetime | None = None) -> ShopifyData:
    rnd = random.Random(seed)
    as_of = (as_of or datetime(2026, 11, 3, 9, 0, 0)).replace(tzinfo=timezone.utc)
    d = ShopifyData()
    cap = as_of - timedelta(minutes=5)                                  # nothing is dated in the future
    tx_id, refund_id = 8_000_000, 5_000_000
    for i in range(n_orders):
        oid = 4_500_000_000_000 + i
        created = as_of - timedelta(days=rnd.uniform(0.3, 30))
        total = round(rnd.uniform(15, 400), 2)
        kind = rnd.choices(["paid", "fulfilled", "pending", "cancelled", "refunded"], [40, 30, 8, 8, 14])[0]
        o = {"id": oid, "created_at": _iso(created), "updated_at": _iso(min(created + timedelta(hours=1), cap)), "cancelled_at": None,
             "financial_status": "paid", "fulfillment_status": None, "total_price": f"{total:.2f}", "currency": "USD",
             "customer": {"id": 7_000_000 + rnd.randint(1, 40)}, "refunds": []}
        txs = []
        if kind in ("paid", "fulfilled", "refunded"):
            tx_id += 1
            txs.append({"id": tx_id, "kind": "sale", "status": "success", "amount": f"{total:.2f}", "currency": "USD",
                        "created_at": _iso(created + timedelta(minutes=2)), "gateway": "bogus"})
        if kind == "fulfilled":
            o["fulfillment_status"] = "fulfilled"
        elif kind == "pending":
            o["financial_status"] = "pending"
        elif kind == "cancelled":
            o["financial_status"], o["cancelled_at"] = "voided", _iso(min(created + timedelta(hours=2), cap))
        elif kind == "refunded":
            refund_id += 1
            tx_id += 1
            amt = round(total * 0.4, 2)
            at = min(created + timedelta(days=1), cap)
            o["refunds"].append({"id": refund_id, "created_at": _iso(at), "processed_at": _iso(at),
                                 "transactions": [{"id": tx_id, "kind": "refund", "status": "success", "amount": f"{amt:.2f}"}]})
            o["financial_status"] = "partially_refunded"
            o["updated_at"] = _iso(at)
        d.orders.append(o)
        d.transactions[oid] = txs

    # injected, labelled problems (orders old enough to be CONFIRMED rather than timing)
    old = [o for o in d.orders if parse_iso(o["created_at"]) < parse_iso(_iso(as_of - timedelta(days=2)))
           and o["financial_status"] == "paid" and not o["refunds"]]
    rnd.shuffle(old)
    miss, partial = old[0], old[1]
    d.transactions[miss["id"]] = []                                             # paid order with no payment transaction
    d.manifest.append({"rule_id": "PAY_MISSING", "entity_id": f"SHP-{miss['id']}"})
    t = d.transactions[partial["id"]][0]
    t["amount"] = f"{float(t['amount']) * 0.5:.2f}"                             # customer only paid half
    d.manifest.append({"rule_id": "PAY_AMOUNT_MISMATCH", "entity_id": f"SHP-{partial['id']}"})
    return d


def create_shopify_mock(data: ShopifyData, faults: Faults | None = None, page_default: int = 250) -> FastAPI:
    app = FastAPI(title="Shopify Admin REST API MOCK (synthetic data)")
    app.state.data, app.state.faults = data, faults or Faults()
    tokens: set[str] = set()
    lock = threading.Lock()
    app.state.tokens = tokens

    def tick() -> JSONResponse | None:
        f: Faults = app.state.faults
        with lock:
            f.calls += 1
            n = f.calls
        if n in f.rate_limit_on:
            return JSONResponse({"errors": "Exceeded 2 calls per second"}, status_code=429, headers={"Retry-After": "0"})
        if n in f.server_error_on or (f.fail_from_call is not None and n >= f.fail_from_call):
            return JSONResponse({"errors": "boom"}, status_code=500)
        return None

    def auth(token: str | None):
        if not token or token not in tokens:
            raise HTTPException(401, "[API] Invalid API key or access token (unrecognized login or wrong password)")

    @app.post("/admin/oauth/access_token")
    async def oauth(request: Request):
        form = await request.form()
        if (form.get("grant_type") != "client_credentials" or form.get("client_id") != CLIENT_ID
                or form.get("client_secret") != CLIENT_SECRET):
            raise HTTPException(400, "invalid_client")
        tok = "shpat_" + secrets.token_hex(12)
        tokens.add(tok)
        return {"access_token": tok, "scope": "read_orders", "expires_in": 86399}

    @app.get("/admin/api/{version}/orders.json")
    def orders(version: str, request: Request, x_shopify_access_token: str | None = Header(default=None)):
        auth(x_shopify_access_token)
        if (err := tick()) is not None:
            return err
        q = request.query_params
        limit = int(q.get("limit", 50))
        fields = q.get("fields")
        if "page_info" in q:
            if any(k in q for k in ("updated_at_min", "updated_at_max", "status")):       # real Shopify rejects this combination
                raise HTTPException(400, "page_info and filter parameters cannot be combined")
            offset = int(base64.urlsafe_b64decode(q["page_info"].encode()).decode())
            rows = app.state.rows
        else:
            lo, hi = q.get("updated_at_min"), q.get("updated_at_max")
            rows = [o for o in app.state.data.orders
                    if (not lo or parse_iso(o["updated_at"]) >= parse_iso(lo)) and (not hi or parse_iso(o["updated_at"]) <= parse_iso(hi))]
            rows.sort(key=lambda o: (parse_iso(o["updated_at"]), o["id"]))
            app.state.rows, offset = rows, 0
        page = rows[offset:offset + limit]
        if fields:
            keep = set(fields.split(","))
            page = [{k: v for k, v in o.items() if k in keep} for o in page]
        headers = {}
        if offset + limit < len(rows):
            nxt = base64.urlsafe_b64encode(str(offset + limit).encode()).decode()
            url = f"{str(request.base_url).rstrip('/')}/admin/api/{version}/orders.json?limit={limit}"
            if fields:
                url += f"&fields={fields}"
            headers["Link"] = f'<{url}&page_info={nxt}>; rel="next"'
        return JSONResponse({"orders": page}, headers=headers)

    @app.get("/admin/api/{version}/orders/{order_id}/transactions.json")
    def transactions(version: str, order_id: int, x_shopify_access_token: str | None = Header(default=None)):
        auth(x_shopify_access_token)
        if (err := tick()) is not None:
            return err
        return {"transactions": app.state.data.transactions.get(order_id, [])}

    @app.get("/health")
    def health():
        return {"status": "ok", "mock": True}

    return app
