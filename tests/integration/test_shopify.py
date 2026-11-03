"""Shopify connector against a local MOCK of the Admin REST API (shape, auth, Link pagination, 429, token refresh)."""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from conftest import AS_OF
from ecomplatform import db
from ecomplatform.config import entity_config, load_sources
from ecomplatform.ingestion.connectors import build_connector
from ecomplatform.ingestion.core.batch_writer import DuckDBSink
from ecomplatform.ingestion.core.retry_policy import AuthError, RetryPolicy
from ecomplatform.ingestion.runner import run_ingestion

pytestmark = pytest.mark.integration
NOW = AS_OF + timedelta(hours=1)
ENTITIES = ["orders", "payments", "refunds"]


def _run(engine, sink, entity, retry=None, **kw):
    cfg = entity_config(load_sources(), "shopify", entity)
    conn = build_connector(cfg, retry=retry or RetryPolicy(max_attempts=4, base_delay=0, jitter=0, sleep=lambda s: None))
    return run_ingestion("shopify", entity, engine=engine, sink=sink, connector=conn, now=NOW, **kw), conn


def _count(sink, fq):
    return int(sink.query_df(f"select count(*) c from {fq}")["c"][0])


def test_ingests_orders_payments_refunds_with_canonical_mapping(shopify, engine, duck_path):
    sink = DuckDBSink(duck_path)
    results = {e: _run(engine, sink, e)[0] for e in ENTITIES}
    assert all(r.status == "SUCCESS" and r.extracted > 0 for r in results.values())
    data = shopify.dataset
    assert results["orders"].loaded == len(data.orders) == _count(sink, "raw_shopify.orders")
    o = data.orders[0]
    row = sink.query_df(f"select * from raw_shopify.orders where order_id = 'SHP-{o['id']}'").iloc[0]
    assert row.total_amount == pytest.approx(float(o["total_price"])) and row.channel == "shopify" and row.currency == "USD"
    from datetime import UTC, datetime
    utc = datetime.fromisoformat(o["created_at"]).astimezone(UTC).replace(tzinfo=None)   # '+07:00' store time -> stored as UTC
    assert row.created_at == utc and o["created_at"].endswith("+07:00")
    statuses = set(sink.query_df("select distinct status from raw_shopify.orders")["status"])
    assert statuses <= {"PENDING", "PAID", "SHIPPED", "COMPLETED", "CANCELLED"} and {"COMPLETED", "PENDING", "CANCELLED"} <= statuses
    pay = sink.query_df("select * from raw_shopify.payments")
    assert (pay.payment_ref == pay.payment_id).all() and pay.order_id.str.startswith("SHP-").all()
    ref = sink.query_df("select * from raw_shopify.refunds")
    assert len(ref) > 0 and (ref.amount > 0).all() and (ref.status == "COMPLETED").all()


def test_link_header_pagination_and_idempotent_rerun(shopify, engine, duck_path):
    sink = DuckDBSink(duck_path)
    cfg = entity_config(load_sources(), "shopify", "orders")
    cfg["page_size"] = 25                                           # 60 orders -> 3 pages through Link headers
    conn = build_connector(cfg)
    r = run_ingestion("shopify", "orders", engine=engine, sink=sink, connector=conn, now=NOW)
    assert r.extracted == 60 and shopify.faults.calls >= 3          # >= 3 order pages (Link headers)
    again = run_ingestion("shopify", "orders", engine=engine, sink=sink, connector=build_connector(cfg), now=NOW,
                          window_start=NOW - timedelta(days=365))
    assert again.loaded == 0 and _count(sink, "raw_shopify.orders") == 60


def test_rate_limit_429_is_retried(shopify, engine, duck_path):
    shopify.faults.rate_limit_on = {1, 2}
    r, _ = _run(engine, DuckDBSink(duck_path), "orders")
    assert r.status == "SUCCESS" and r.retries == 2


def test_expired_token_is_refreshed_once(shopify, engine, duck_path):
    sink = DuckDBSink(duck_path)
    r, conn = _run(engine, sink, "orders")
    assert r.status == "SUCCESS"
    shopify.app.state.tokens.clear()                                # server forgets the token (24 h expiry)
    r2, _ = run_ingestion("shopify", "orders", engine=engine, sink=sink, connector=conn, now=NOW + timedelta(hours=1)), None
    assert r2.status == "SUCCESS"


def test_bad_credentials_fail_cleanly_without_leaking_secret(shopify, engine, duck_path, monkeypatch):
    monkeypatch.setenv("SHOPIFY_CLIENT_SECRET", "WRONG-SECRET-123456")
    with pytest.raises(AuthError) as e:
        _run(engine, DuckDBSink(duck_path), "orders")
    assert "WRONG-SECRET-123456" not in str(e.value)
    with engine.connect() as c:
        stored = " ".join(str(v) for row in c.execute(select(db.pipeline_run_audit)).mappings() for v in row.values())
    assert "WRONG-SECRET-123456" not in stored and "AUTH" in stored


def test_static_token_and_missing_config_messages(shopify, monkeypatch):
    cfg = entity_config(load_sources(), "shopify", "orders")
    monkeypatch.delenv("SHOPIFY_CLIENT_ID")
    with pytest.raises(AuthError, match="SHOPIFY_ACCESS_TOKEN"):
        build_connector(cfg)
    monkeypatch.delenv("SHOPIFY_STORE_URL")
    with pytest.raises(Exception, match="SHOPIFY_STORE_URL"):
        build_connector(cfg)
