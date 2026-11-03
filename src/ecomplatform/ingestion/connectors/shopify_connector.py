"""Shopify Admin REST API connector (orders, payments, refunds).

Auth (either):
  * SHOPIFY_ACCESS_TOKEN                      - a static Admin API token (legacy custom apps)
  * SHOPIFY_CLIENT_ID + SHOPIFY_CLIENT_SECRET - Dev Dashboard app, client-credentials grant (token valid 24 h, refreshed
                                                automatically, also once after a 401)
Store: SHOPIFY_STORE_URL=https://<store>.myshopify.com      Version: SHOPIFY_API_VERSION (default below)

Notes
  * Shopify marks the REST Admin API as legacy (GraphQL is the long-term API); custom/merchant apps may still use REST.
  * `read_orders` only returns the last 60 days; add the `read_all_orders` scope for older orders.
  * There is no bulk transactions endpoint in REST, so payments cost one request per order in the window (rate limited:
    HTTP 429 + Retry-After is honoured by the retry policy).
  * Payouts/settlements (Shopify Payments) are not ingested: development stores have no payouts.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime
from typing import Any

import requests

from ecomplatform.ingestion.base_connector import RestConnector
from ecomplatform.ingestion.core.api_client import ApiClient
from ecomplatform.ingestion.core.pagination import link_header_pages
from ecomplatform.ingestion.core.retry_policy import AuthError, PermanentError, RetryPolicy
from ecomplatform.util import redact, to_iso

DEFAULT_API_VERSION = "2026-01"
ORDER_FIELDS = "id,created_at,updated_at,cancelled_at,financial_status,fulfillment_status,total_price,currency,customer,refunds"


def _id(prefix: str, value: Any) -> str:
    return f"{prefix}{value}"


def order_status(o: dict) -> str:
    """Shopify (financial_status, fulfillment_status, cancelled_at) -> platform vocabulary."""
    if o.get("cancelled_at"):
        return "CANCELLED"
    fin, ful = (o.get("financial_status") or "").lower(), (o.get("fulfillment_status") or "").lower()
    if fin == "voided":
        return "CANCELLED"
    if ful == "fulfilled":
        return "COMPLETED"
    if ful in ("partial", "in_progress", "on_hold"):
        return "SHIPPED"
    if fin in ("paid", "partially_paid", "partially_refunded", "refunded"):
        return "PAID"
    return "PENDING"                                   # pending / authorized (not captured yet) / unknown


def _order(raw: dict) -> dict:
    cust = raw.get("customer") or {}
    return {
        "order_id": _id("SHP-", raw["id"]), "channel": "shopify", "status": order_status(raw),
        "total_amount": None if raw.get("total_price") is None else round(float(raw["total_price"]), 2),
        "currency": raw.get("currency"), "customer_id": _id("SHPC-", cust["id"]) if cust.get("id") else None,
        "created_at": raw.get("created_at"), "updated_at": raw.get("updated_at"),
    }


def _payment(raw: dict) -> dict:
    return {
        "payment_id": _id("SHPTX-", raw["id"]), "order_id": _id("SHP-", raw["order_id"]), "payment_ref": _id("SHPTX-", raw["id"]),
        "amount": round(float(raw["amount"]), 2), "currency": raw.get("currency"),
        "paid_at": raw.get("created_at"), "updated_at": raw.get("created_at"),
    }


def _refund(raw: dict) -> dict:
    return {
        "refund_id": _id("SHPRF-", raw["id"]), "order_id": _id("SHP-", raw["order_id"]), "refund_ref": _id("SHPRF-", raw["id"]),
        "amount": round(float(raw["amount"]), 2), "currency": raw.get("currency"), "status": "COMPLETED",
        "refunded_at": raw["created_at"], "updated_at": raw["created_at"],
    }


class ShopifyTokenProvider:
    """Client-credentials grant for Dev Dashboard apps (the app and the store must be in the same organization)."""

    def __init__(self, store_url: str, client_id: str, client_secret: str, session: requests.Session | None = None,
                 timeout: float = 15.0):
        self.url = f"{store_url.rstrip('/')}/admin/oauth/access_token"
        self.client_id, self.client_secret = client_id, client_secret
        self.session, self.timeout = session or requests.Session(), timeout

    def __call__(self) -> str:
        secrets = [self.client_secret]
        try:
            r = self.session.post(self.url, timeout=self.timeout, data={
                "grant_type": "client_credentials", "client_id": self.client_id, "client_secret": self.client_secret})
        except requests.RequestException as exc:
            raise AuthError(redact(f"token request failed: {type(exc).__name__}", secrets)) from None
        if r.status_code != 200 or "access_token" not in (r.json() if r.content else {}):
            raise AuthError(f"Shopify token request failed (HTTP {r.status_code}): check client id/secret, scopes, "
                            f"and that the app is installed on this store")
        return r.json()["access_token"]


class ShopifyConnector(RestConnector):
    normalizers = {"orders": _order, "payments": _payment, "refunds": _refund}

    @classmethod
    def build_client(cls, cfg: dict[str, Any], retry: RetryPolicy | None = None) -> ApiClient:
        store = os.getenv(cfg.get("base_url_env", "SHOPIFY_STORE_URL"), "").strip()
        if not store:
            raise PermanentError("SHOPIFY_STORE_URL is not set (expected https://<store>.myshopify.com)")
        if not store.startswith("http"):
            store = f"https://{store}"
        token = os.getenv(cfg.get("token_env", "SHOPIFY_ACCESS_TOKEN")) or None
        provider = None
        if not token:
            cid, secret = os.getenv("SHOPIFY_CLIENT_ID"), os.getenv("SHOPIFY_CLIENT_SECRET")
            if not (cid and secret):
                raise AuthError("set SHOPIFY_ACCESS_TOKEN, or SHOPIFY_CLIENT_ID + SHOPIFY_CLIENT_SECRET")
            provider = ShopifyTokenProvider(store, cid, secret)
        return ApiClient(store, token=token, token_provider=provider, auth_header="X-Shopify-Access-Token", auth_prefix="",
                         timeout=cfg.get("timeout_seconds", 15), retry=retry)

    # -------------------------------------------------------------------------------- extraction
    def _version(self) -> str:
        return os.getenv("SHOPIFY_API_VERSION") or self.cfg.get("api_version") or DEFAULT_API_VERSION

    def _orders(self, since: datetime, until: datetime, fields: str) -> Iterator[list[dict]]:
        params = {"status": "any", "limit": min(250, self.cfg.get("page_size", 250)), "fields": fields,
                  "updated_at_min": to_iso(since), "updated_at_max": to_iso(until)}
        yield from link_header_pages(self.client, f"/admin/api/{self._version()}/orders.json", params, items_key="orders")

    def fetch_pages(self, since: datetime, until: datetime) -> Iterator[list[dict]]:
        if self.entity == "orders":
            yield from self._orders(since, until, ORDER_FIELDS)
        elif self.entity == "refunds":
            for page in self._orders(since, until, "id,currency,refunds"):
                out = []
                for o in page:
                    for r in o.get("refunds") or []:
                        amount = sum(float(t["amount"]) for t in r.get("transactions") or []
                                     if t.get("kind") == "refund" and t.get("status") == "success")
                        if amount > 0:
                            out.append({"id": r["id"], "order_id": o["id"], "currency": o.get("currency"), "amount": amount,
                                        "created_at": r.get("processed_at") or r["created_at"]})
                yield out
        elif self.entity == "payments":
            for page in self._orders(since, until, "id"):
                out = []
                for o in page:                                  # no bulk endpoint: one call per order in the window
                    body = self.client.get_json(f"/admin/api/{self._version()}/orders/{o['id']}/transactions.json")
                    out += [{**t, "order_id": o["id"]} for t in body.get("transactions", [])
                            if t.get("kind") in ("sale", "capture") and t.get("status") == "success"]
                yield out
        else:
            raise PermanentError(f"unsupported Shopify entity: {self.entity}")
