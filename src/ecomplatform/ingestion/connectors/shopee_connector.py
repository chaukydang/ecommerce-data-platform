"""Shopee Open Platform API v2 connector (orders, buyer payments, escrow/settlements) - the marketplace for SEA.

Credentials (from your Shopee Open Platform app + shop authorization, see docs/shopee_quickstart.md):
  SHOPEE_PARTNER_ID, SHOPEE_PARTNER_KEY, SHOPEE_SHOP_ID, SHOPEE_REFRESH_TOKEN
  SHOPEE_API_HOST   (default: sandbox https://partner.test-stable.shopeemobile.com ; production https://partner.shopeemobile.com)
  SHOPEE_TOKEN_FILE (default .shopee_tokens.json) - refresh tokens ROTATE on every refresh, so the newest one is kept there

Protocol (documented by Shopee): every call carries partner_id, timestamp and sign = HMAC-SHA256(partner_key,
partner_id + api_path + timestamp [+ access_token + shop_id for shop APIs]); access tokens live 4 h; errors come back as HTTP 200
with a non-empty "error" field. get_order_list accepts a time window of at most 15 days.

STATUS: written from Shopee's public documentation and verified against a local mock only (src/ecomplatform/simulators/shopee_mock.py).
Field names marked "verify" below should be confirmed on the first run against the Shopee sandbox / your shop.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from ecomplatform.ingestion.base_connector import RestConnector
from ecomplatform.ingestion.core.api_client import ApiClient
from ecomplatform.ingestion.core.retry_policy import AuthError, PermanentError, RetryPolicy, TransientError
from ecomplatform.util import redact, to_iso

UTC = timezone.utc

SANDBOX = "https://partner.test-stable.shopeemobile.com"
MAX_WINDOW = timedelta(days=15)
ORDER_STATUS = {"UNPAID": "PENDING", "PENDING": "PENDING", "READY_TO_SHIP": "PAID", "PROCESSED": "PAID", "INVOICE_PENDING": "PAID",
                "SHIPPED": "SHIPPED", "TO_CONFIRM_RECEIVE": "SHIPPED", "COMPLETED": "COMPLETED", "TO_RETURN": "COMPLETED",
                "IN_CANCEL": "CANCELLED", "CANCELLED": "CANCELLED"}
TRANSIENT_ERRORS = {"error_server", "error_inner", "error_busy", "error_too_many_request", "error_rate_limit", "error_network"}
AUTH_ERRORS = {"error_auth", "error_permission", "error_sign"}


def sign(partner_key: str, base: str) -> str:
    return hmac.new(partner_key.encode(), base.encode(), hashlib.sha256).hexdigest()


def _iso(epoch: int | float | None) -> str | None:
    return None if not epoch else to_iso(datetime.fromtimestamp(int(epoch), UTC).replace(tzinfo=None))


def _num(v: Any) -> float:
    return float(v or 0)


def body_check(body: dict) -> None:
    """Shopee reports failures as HTTP 200 + {"error": "error_xxx", "message": ...}."""
    err = body.get("error")
    if not err:
        return
    msg = f"Shopee {err}: {body.get('message', '')}"[:300]
    if err in TRANSIENT_ERRORS:
        raise TransientError(msg, retry_after=1.0 if err in ("error_too_many_request", "error_rate_limit") else None)
    if err in AUTH_ERRORS:
        raise AuthError(msg)
    raise PermanentError(msg)


# ------------------------------------------------------------------------------------------------ normalisers
def _order(raw: dict) -> dict:
    return {"order_id": f"SPE-{raw['order_sn']}", "channel": "shopee",
            "status": ORDER_STATUS.get(str(raw.get("order_status", "")).upper(), "PENDING"),
            "total_amount": None if raw.get("total_amount") is None else round(float(raw["total_amount"]), 2),
            "currency": raw.get("currency"), "customer_id": f"SPEB-{raw['buyer_user_id']}" if raw.get("buyer_user_id") else None,
            "created_at": _iso(raw.get("create_time")), "updated_at": _iso(raw.get("update_time"))}


def _payment(raw: dict) -> dict:
    ref = f"SPEPAY-{raw['order_sn']}"
    return {"payment_id": ref, "order_id": f"SPE-{raw['order_sn']}", "payment_ref": ref, "amount": round(float(raw["total_amount"]), 2),
            "currency": raw.get("currency"), "paid_at": _iso(raw["pay_time"]), "updated_at": _iso(raw.get("update_time") or raw["pay_time"])}


def _settlement(raw: dict) -> dict:
    """order_income (get_escrow_detail) -> platform settlement components.

    Shopee's documented escrow formula is:
      escrow = buyer_total + shopee_discount + voucher_from_shopee + coins + payment_promotion - buyer_transaction_fee
               - cross_border_tax - commission_fee - service_fee - seller_transaction_fee - seller_coin_cash_back
               - escrow_tax - drc_adjustable_refund + final_shipping_fee
    gross = buyer_total_amount, fees = commission + service + seller transaction fee, adjustment = every other signed component,
    so the platform's default formula (gross - discount - refund - commission - shipping + adjustment) reproduces it.  (verify names)
    """
    i = raw["order_income"]
    adjustment = (_num(i.get("shopee_discount")) + _num(i.get("voucher_from_shopee")) + _num(i.get("coins")) + _num(i.get("payment_promotion"))
                  + _num(i.get("final_shipping_fee")) - _num(i.get("buyer_transaction_fee")) - _num(i.get("cross_border_tax"))
                  - _num(i.get("seller_coin_cash_back")) - _num(i.get("escrow_tax")) - _num(i.get("drc_adjustable_refund")))
    return {"settlement_id": f"SPEESC-{raw['order_sn']}", "order_id": f"SPE-{raw['order_sn']}", "channel": "shopee",
            "gross_amount": round(_num(i.get("buyer_total_amount")), 2), "seller_discount": 0.0, "refund_amount": 0.0,
            "commission_fee": round(_num(i.get("commission_fee")) + _num(i.get("service_fee")) + _num(i.get("seller_transaction_fee")), 2),
            "shipping_fee": 0.0, "adjustment_amount": round(adjustment, 2), "net_payout": round(_num(i.get("escrow_amount")), 2),
            "currency": raw.get("currency"), "settled_at": _iso(raw.get("update_time")), "updated_at": _iso(raw.get("update_time"))}


class ShopeeCredentials:
    """Partner key + shop + rotating tokens. Reads/writes the token file; secrets are never logged."""

    def __init__(self, session: requests.Session | None = None):
        self.partner_id = os.getenv("SHOPEE_PARTNER_ID", "")
        self.partner_key = os.getenv("SHOPEE_PARTNER_KEY", "")
        self.shop_id = os.getenv("SHOPEE_SHOP_ID", "")
        if not (self.partner_id and self.partner_key and self.shop_id):
            raise AuthError("set SHOPEE_PARTNER_ID, SHOPEE_PARTNER_KEY, SHOPEE_SHOP_ID and SHOPEE_REFRESH_TOKEN (docs/shopee_quickstart.md)")
        self.host = os.getenv("SHOPEE_API_HOST", SANDBOX).rstrip("/")
        self.token_file = Path(os.getenv("SHOPEE_TOKEN_FILE", ".shopee_tokens.json"))
        self.session = session or requests.Session()
        self._refresh_token = self._load_refresh_token()

    def _load_refresh_token(self) -> str:
        if self.token_file.exists():
            try:
                saved = json.loads(self.token_file.read_text())
                if saved.get("shop_id") == self.shop_id and saved.get("refresh_token"):
                    return saved["refresh_token"]
            except (OSError, ValueError):
                pass
        tok = os.getenv("SHOPEE_REFRESH_TOKEN", "")
        if not tok:
            raise AuthError("SHOPEE_REFRESH_TOKEN is not set (authorize your shop once, see docs/shopee_quickstart.md)")
        return tok

    def _save(self, refresh_token: str) -> None:
        self.token_file.write_text(json.dumps({"shop_id": self.shop_id, "refresh_token": refresh_token}))
        try:
            self.token_file.chmod(0o600)
        except OSError:
            pass

    def sign_params(self, path: str, params: dict[str, Any], access_token: str | None) -> dict[str, Any]:
        ts = int(time.time())
        base = f"{self.partner_id}{path}{ts}{access_token or ''}{self.shop_id if access_token else ''}"
        out = {**params, "partner_id": self.partner_id, "timestamp": ts, "sign": sign(self.partner_key, base)}
        if access_token:
            out.update(access_token=access_token, shop_id=self.shop_id)
        return out

    def __call__(self) -> str:
        """Refresh the access token (4 h). The refresh token rotates, so persist the new one."""
        path, ts = "/api/v2/auth/access_token/get", int(time.time())
        params = {"partner_id": self.partner_id, "timestamp": ts, "sign": sign(self.partner_key, f"{self.partner_id}{path}{ts}")}
        secrets = [self.partner_key, self._refresh_token]
        try:
            r = self.session.post(f"{self.host}{path}", params=params, timeout=15, json={
                "refresh_token": self._refresh_token, "partner_id": int(self.partner_id), "shop_id": int(self.shop_id)})
            body = r.json()
        except (requests.RequestException, ValueError) as exc:
            raise AuthError(redact(f"Shopee token refresh failed: {type(exc).__name__}", secrets)) from None
        if body.get("error") or "access_token" not in body:
            raise AuthError(f"Shopee token refresh failed ({body.get('error')}): the refresh token may have expired (30 days) - re-authorize the shop")
        if body.get("refresh_token"):
            self._refresh_token = body["refresh_token"]
            self._save(self._refresh_token)
        return body["access_token"]


class ShopeeConnector(RestConnector):
    normalizers = {"orders": _order, "payments": _payment, "settlements": _settlement}

    @classmethod
    def build_client(cls, cfg: dict[str, Any], retry: RetryPolicy | None = None) -> ApiClient:
        creds = ShopeeCredentials()
        return ApiClient(creds.host, token=None, token_provider=creds, auth_header=None, signer=creds.sign_params,
                         body_check=body_check, timeout=cfg.get("timeout_seconds", 20), retry=retry)

    # --------------------------------------------------------------------------------------- extraction
    def _windows(self, since: datetime, until: datetime) -> Iterator[tuple[int, int]]:
        t0 = since
        while t0 < until:
            t1 = min(t0 + MAX_WINDOW - timedelta(seconds=1), until)               # get_order_list: at most 15 days per call
            yield int(t0.replace(tzinfo=UTC).timestamp()), int(t1.replace(tzinfo=UTC).timestamp())
            t0 = t1 + timedelta(seconds=1)

    def _order_details(self, since: datetime, until: datetime) -> Iterator[list[dict]]:
        for t0, t1 in self._windows(since, until):
            sns: list[str] = []
            cursor = ""
            while True:
                body = self.client.get_json("/api/v2/order/get_order_list", {
                    "time_range_field": "update_time", "time_from": t0, "time_to": t1, "page_size": 100, "cursor": cursor})
                resp = body.get("response", {})
                sns += [o["order_sn"] for o in resp.get("order_list", [])]
                if not resp.get("more"):
                    break
                cursor = resp.get("next_cursor", "")
            for i in range(0, len(sns), 50):                                     # get_order_detail: up to 50 order_sn per call
                detail = self.client.get_json("/api/v2/order/get_order_detail", {
                    "order_sn_list": ",".join(sns[i:i + 50]), "response_optional_fields": "total_amount,pay_time,buyer_user_id"})
                yield detail.get("response", {}).get("order_list", [])

    def fetch_pages(self, since: datetime, until: datetime) -> Iterator[list[dict]]:
        if self.entity == "orders":
            yield from self._order_details(since, until)
        elif self.entity == "payments":
            for page in self._order_details(since, until):
                yield [o for o in page if o.get("pay_time") and o.get("total_amount") is not None]
        elif self.entity == "settlements":
            for page in self._order_details(since, until):
                out = []
                for o in page:
                    if str(o.get("order_status", "")).upper() != "COMPLETED":     # escrow is final only for completed orders
                        continue
                    try:
                        body = self.client.get_json("/api/v2/payment/get_escrow_detail", {"order_sn": o["order_sn"]})
                    except PermanentError:             # escrow not available for this order (yet): no settlement record, the
                        continue                       # reconciliation rule SETTLE_MISSING reports it once it is overdue
                    income = body.get("response", {}).get("order_income")
                    if income:
                        out.append({"order_sn": o["order_sn"], "currency": o.get("currency"), "update_time": o.get("update_time"),
                                    "order_income": income})
                yield out
        else:
            raise PermanentError(f"unsupported Shopee entity: {self.entity}")
