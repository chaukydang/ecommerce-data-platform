"""Omnichannel demo data (SIMULATED): Shopee, TikTok Shop, Lazada, Website and POS stores, in VND, last 12 months.

Not a production integration - it exists so the dashboard can show a multi-channel business without anyone's private data.
The shape is realistic on purpose: growth, weekends, payday, mega-sale days (3.3 ... 12.12, 11.11, Black Friday), the Tet
slow-down, per-channel basket size / cancel / return rates, and a handful of *planted* payment / refund problems so the
reconciliation page has something real to find.

Deterministic: every (day, channel) is generated from its own seed, so re-running ingestion is idempotent and only new days appear.
Records come out already canonical (same fields as the Olist / Shopify profiles): orders, payments, refunds.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np

from ecomplatform.ingestion.base_connector import BaseConnector
from ecomplatform.ingestion.core.retry_policy import PermanentError, RetryPolicy

from .olist_connector import _FileClient

UTC = timezone.utc
DAYS = 365
CHUNK = 5000
CURRENCY = "VND"


@dataclass(frozen=True)
class Channel:
    name: str
    share: float          # share of orders
    aov: float            # typical basket, VND
    cancel: float         # cancelled before shipping
    refund: float         # refunded after delivery
    online: bool = True   # online channels react to mega-sales; POS does not
    growth: float = 0.0   # extra yearly growth on top of the common trend


CHANNELS = [
    Channel("Shopee", 0.36, 280_000, 0.060, 0.045, growth=0.25),
    Channel("TikTok Shop", 0.24, 220_000, 0.090, 0.070, growth=0.90),
    Channel("Lazada", 0.12, 310_000, 0.070, 0.050, growth=-0.05),
    Channel("Website", 0.10, 450_000, 0.030, 0.030, growth=0.20),
    Channel("POS (stores)", 0.18, 380_000, 0.010, 0.015, online=False, growth=0.05),
]
BASE_DAILY_ORDERS = 170.0
SEED = 20261104
# planted problems (probability per eligible order) - reconciliation must find them
P_MISSING_PAYMENT, P_AMOUNT_MISMATCH, P_CURRENCY_MISMATCH, P_DUPLICATE_PAYMENT, P_REFUND_EXCEEDS = 0.0008, 0.0008, 0.0003, 0.0003, 0.0015

SALE_DAYS = {(m, m) for m in range(3, 13)}                       # 3.3, 4.4 ... 12.12
HOURS_ONLINE = np.array([1, 1, 1, 1, 1, 2, 3, 4, 5, 6, 7, 8, 9, 7, 6, 6, 7, 8, 9, 11, 13, 12, 8, 3], dtype=float)
HOURS_POS = np.array([0, 0, 0, 0, 0, 0, 0, 1, 3, 6, 8, 9, 7, 6, 7, 8, 9, 10, 11, 10, 6, 1, 0, 0], dtype=float)


def _event_factor(day: datetime, ch: Channel) -> float:
    """Calendar effects on the expected number of orders for one day."""
    f = 1.0
    wd = day.weekday()
    f *= (1.18 if wd >= 5 else 1.0) if not ch.online else (1.10 if wd in (4, 5, 6) else 1.0)
    if day.day <= 3 or day.day >= 25:
        f *= 1.10                                                # payday
    if ch.online:
        if (day.month, day.day) in {(11, 11), (12, 12)}:
            f *= 3.2
        elif (day.month, day.day) in SALE_DAYS:
            f *= 2.2
        elif (day.month, day.day) == (11, 28) or (day.month == 11 and 24 <= day.day <= 28 and wd == 4):
            f *= 1.8                                             # Black Friday
    d = day.date()
    if d >= datetime(2026, 1, 25).date() and d <= datetime(2026, 2, 10).date():
        f *= 1.35                                                # pre-Tet shopping
    if d >= datetime(2026, 2, 14).date() and d <= datetime(2026, 2, 22).date():
        f *= 0.35 if ch.online else 0.25                         # Tet holiday
    return f


def _round_vnd(x: float) -> float:
    return float(round(x / 1000.0) * 1000)


def _iso(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def generate(as_of: datetime) -> dict[str, list[dict]]:
    """All orders / payments / refunds up to ``as_of`` (UTC, naive or aware)."""
    as_of = as_of.astimezone(UTC).replace(tzinfo=None) if as_of.tzinfo else as_of
    end_day = datetime(as_of.year, as_of.month, as_of.day)
    start_day = end_day - timedelta(days=DAYS - 1)
    orders: list[dict] = []
    payments: list[dict] = []
    refunds: list[dict] = []
    for di in range(DAYS):
        day = start_day + timedelta(days=di)
        t = di / DAYS                                            # 0 -> 1 across the window
        for ci, ch in enumerate(CHANNELS):
            rng = np.random.default_rng([SEED, day.toordinal(), ci])
            trend = (1.0 + 0.45 * t) * (1.0 + ch.growth * t)
            lam = BASE_DAILY_ORDERS * ch.share * trend * _event_factor(day, ch)
            n = int(rng.poisson(lam))
            if n == 0:
                continue
            hours = rng.choice(24, size=n, p=(HOURS_ONLINE if ch.online else HOURS_POS) / (HOURS_ONLINE if ch.online else HOURS_POS).sum())
            secs = rng.integers(0, 3600, size=n)
            amount = np.array([_round_vnd(a) for a in rng.lognormal(np.log(ch.aov), 0.55, size=n)]).clip(30_000)
            cust = rng.integers(1, 60_000, size=n)
            roll = rng.random((n, 8))
            lag = rng.uniform(1.5, 6.0, size=n)                  # days until delivered (online)
            tag = "".join(w[0] for w in ch.name.split()).upper()[:3]
            for k in range(n):
                created = day + timedelta(hours=int(hours[k]), seconds=int(secs[k]))
                if created > as_of:
                    continue
                oid = f"{tag}-{day:%y%m%d}-{k + 1:04d}"
                total = float(amount[k])
                age_d = (as_of - created).total_seconds() / 86400
                r = roll[k]
                if not ch.online:                                # POS: paid and handed over at the till
                    status, delivered = "COMPLETED", created + timedelta(minutes=5)
                elif r[0] < ch.cancel:
                    status, delivered = "CANCELLED", None
                    updated = created + timedelta(hours=float(r[1]) * 20 + 1)
                else:
                    delivered = created + timedelta(days=float(lag[k]))
                    if age_d < 0.4 and r[1] < 0.3:
                        status = "PENDING"
                    elif delivered <= as_of:
                        status = "COMPLETED"
                    elif age_d > 1.0:
                        status = "SHIPPED"
                    else:
                        status = "PAID"
                if status == "CANCELLED":
                    pass
                elif status == "PENDING":
                    updated = created
                elif status == "PAID":
                    updated = created + timedelta(minutes=10)
                elif status == "SHIPPED":
                    updated = created + timedelta(days=1)
                else:
                    updated = delivered
                updated = min(updated, as_of - timedelta(minutes=1))
                orders.append({"order_id": oid, "channel": ch.name, "status": status, "total_amount": total, "currency": CURRENCY,
                               "customer_id": f"C-{int(cust[k]):05d}", "created_at": _iso(created), "updated_at": _iso(updated)})
                paid = status in ("PAID", "SHIPPED", "COMPLETED")
                grace_over = age_d > 1.0
                pay_amount, pay_cur, p_at = total, CURRENCY, created + timedelta(minutes=2 if not ch.online else 1 + int(r[2] * 25))
                missing = False
                if paid and grace_over:                          # planted problems only on orders old enough to be judged
                    if r[3] < P_MISSING_PAYMENT:
                        missing = True
                    elif r[3] < P_MISSING_PAYMENT + P_AMOUNT_MISMATCH:
                        pay_amount = _round_vnd(total * 0.9)
                    elif r[3] < P_MISSING_PAYMENT + P_AMOUNT_MISMATCH + P_CURRENCY_MISMATCH:
                        pay_cur = "USD"
                if paid and not missing:
                    p_at = min(p_at, as_of - timedelta(minutes=1))
                    payments.append({"payment_id": f"PY-{oid}", "order_id": oid, "payment_ref": f"PAY-{oid}", "amount": pay_amount,
                                     "currency": pay_cur, "paid_at": _iso(p_at), "updated_at": _iso(p_at)})
                    if grace_over and r[4] < P_DUPLICATE_PAYMENT:
                        payments.append({"payment_id": f"PY-{oid}-DUP", "order_id": oid, "payment_ref": f"PAY-{oid}", "amount": pay_amount,
                                         "currency": pay_cur, "paid_at": _iso(p_at), "updated_at": _iso(p_at)})
                if status == "COMPLETED" and not missing and r[5] < ch.refund and delivered is not None:
                    r_at = delivered + timedelta(days=1 + float(r[6]) * 6)
                    if r_at <= as_of - timedelta(minutes=1):
                        part = 1.0 if r[7] < 0.45 else 0.3 + 0.5 * float(r[7])
                        amt = _round_vnd(total * part)
                        if r[7] > 1 - P_REFUND_EXCEEDS * 5:      # planted: refund bigger than what was paid
                            amt = _round_vnd(total * 1.5)
                        refunds.append({"refund_id": f"RF-{oid}", "order_id": oid, "refund_ref": f"RF-{oid}", "amount": amt,
                                        "currency": CURRENCY, "status": "COMPLETED", "refunded_at": _iso(r_at), "updated_at": _iso(r_at)})
    return {"orders": orders, "payments": payments, "refunds": refunds}


_CACHE: dict[Any, dict[str, list[dict]]] = {}


def _dataset(as_of: datetime) -> dict[str, list[dict]]:
    key = as_of.replace(minute=0, second=0, microsecond=0)        # same snapshot for orders / payments / refunds of one run
    if key not in _CACHE:
        _CACHE.clear()
        _CACHE[key] = generate(key)
    return _CACHE[key]


class OmniSimConnector(BaseConnector):
    @classmethod
    def build_client(cls, cfg: dict[str, Any], retry: RetryPolicy | None = None) -> _FileClient:
        return _FileClient(retry)

    def normalize(self, raw: dict) -> dict:
        return dict(raw)

    def fetch_pages(self, since: datetime, until: datetime) -> Iterator[list[dict]]:
        if self.entity not in ("orders", "payments", "refunds"):
            raise PermanentError(f"unsupported omni entity: {self.entity}")
        as_of = datetime.fromisoformat(os.environ["OMNI_AS_OF"]) if os.getenv("OMNI_AS_OF") else until
        lo, hi = _iso(since.astimezone(UTC) if since.tzinfo else since), _iso(until.astimezone(UTC) if until.tzinfo else until)
        rows = [r for r in _dataset(as_of)[self.entity] if lo <= r["updated_at"] <= hi]
        for i in range(0, len(rows), CHUNK):
            yield rows[i:i + CHUNK]


__all__ = ["OmniSimConnector", "generate", "CHANNELS"]
