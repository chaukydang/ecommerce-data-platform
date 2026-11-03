"""Shopee connector against a local MOCK of Open Platform v2 (HMAC signing, rotating tokens, 200-body errors, 15-day windows)."""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from conftest import AS_OF
from ecomplatform import db
from ecomplatform.config import entity_config, load_sources
from ecomplatform.ingestion.connectors import build_connector
from ecomplatform.ingestion.core.batch_writer import DuckDBSink
from ecomplatform.ingestion.core.retry_policy import AuthError, RetryPolicy
from ecomplatform.ingestion.runner import run_ingestion
from ecomplatform.profile_pipeline import PROFILES, run_profile
from ecomplatform.reconciliation.store import ExceptionStore

UTC = timezone.utc

pytestmark = pytest.mark.integration
NOW = AS_OF + timedelta(hours=1)
FAST = RetryPolicy(max_attempts=4, base_delay=0, jitter=0, sleep=lambda s: None)


def _run(engine, sink, entity, **kw):
    conn = build_connector(entity_config(load_sources(), "shopee", entity), retry=FAST)
    return run_ingestion("shopee", entity, engine=engine, sink=sink, connector=conn, now=NOW, **kw)


def _count(sink, fq):
    return int(sink.query_df(f"select count(*) c from {fq}")["c"][0])


def test_signed_calls_orders_payments_settlements_and_windows(shopee, engine, duck_path):
    sink = DuckDBSink(duck_path)
    res = {e: _run(engine, sink, e) for e in ("orders", "payments", "settlements")}
    assert all(r.status == "SUCCESS" for r in res.values()), res
    data = shopee.dataset
    assert res["orders"].loaded == len(data.orders) == _count(sink, "raw_shopee.orders")
    assert shopee.app.state.window_calls >= 3 * 2          # 40 days of data => >= 3 windows of <= 15 days, for each entity pass
    o = next(iter(data.orders.values()))
    row = sink.query_df(f"select * from raw_shopee.orders where order_id = 'SPE-{o['order_sn']}'").iloc[0]
    assert row.total_amount == pytest.approx(o["total_amount"]) and row.currency == "VND" and row.channel == "shopee"
    assert row.created_at == datetime.fromtimestamp(o["create_time"], UTC).replace(tzinfo=None)         # epoch -> UTC
    assert set(sink.query_df("select distinct status from raw_shopee.orders").status) <= {"PENDING", "PAID", "SHIPPED", "COMPLETED", "CANCELLED"}
    # payments only for orders that have a pay_time; settlements only for completed orders with escrow
    assert res["payments"].loaded == sum(1 for x in data.orders.values() if x.get("pay_time"))
    assert res["settlements"].loaded == len(data.escrow)
    s = sink.query_df("select * from raw_shopee.settlements limit 1").iloc[0]
    assert s.channel == "shopee" and s.net_payout > 0 and s.commission_fee > 0


def test_idempotent_and_refresh_token_rotation_is_persisted(shopee, engine, duck_path, tmp_path):
    sink = DuckDBSink(duck_path)
    first_refresh = shopee.state["refresh"]
    r1 = _run(engine, sink, "orders")
    saved = json.loads(Path(tmp_path / "shopee_tokens.json").read_text())
    assert saved["refresh_token"] == shopee.state["refresh"] != first_refresh                       # rotated and saved
    again = run_ingestion("shopee", "orders", engine=engine, sink=sink, now=NOW, window_start=NOW - timedelta(days=60),
                          connector=build_connector(entity_config(load_sources(), "shopee", "orders"), retry=FAST))
    assert again.loaded == 0 and _count(sink, "raw_shopee.orders") == r1.loaded                     # idempotent; new run used the saved token


def test_expired_access_token_is_refreshed_mid_run(shopee, engine, duck_path):
    sink = DuckDBSink(duck_path)
    conn = build_connector(entity_config(load_sources(), "shopee", "orders"), retry=FAST)
    assert run_ingestion("shopee", "orders", engine=engine, sink=sink, connector=conn, now=NOW).status == "SUCCESS"
    shopee.state["tokens"].clear()                                                                   # 4 h expiry
    assert run_ingestion("shopee", "orders", engine=engine, sink=sink, connector=conn, now=NOW + timedelta(hours=5)).status == "SUCCESS"


def test_errors_in_200_bodies_are_classified(shopee, engine, duck_path):
    shopee.faults.rate_limit_on = {1, 2}
    shopee.faults.server_error_on = {3}
    r = _run(engine, DuckDBSink(duck_path), "orders")
    assert r.status == "SUCCESS" and r.retries == 3                        # error_too_many_request / error_server are retried


def test_wrong_partner_key_fails_without_leaking_secrets(shopee, engine, duck_path, monkeypatch):
    monkeypatch.setenv("SHOPEE_PARTNER_KEY", "WRONG-PARTNER-KEY-987654")
    with pytest.raises(AuthError) as e:
        _run(engine, DuckDBSink(duck_path), "orders")
    assert "WRONG-PARTNER-KEY-987654" not in str(e.value)
    with engine.connect() as c:
        stored = " ".join(str(v) for row in c.execute(select(db.pipeline_run_audit)).mappings() for v in row.values())
    assert "WRONG-PARTNER-KEY-987654" not in stored and "AUTH" in stored


def test_missing_credentials_message(monkeypatch):
    for k in ("SHOPEE_PARTNER_ID", "SHOPEE_PARTNER_KEY", "SHOPEE_SHOP_ID"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(AuthError, match="SHOPEE_PARTNER_ID"):
        build_connector(entity_config(load_sources(), "shopee", "orders"))


@pytest.mark.dbt
@pytest.mark.skipif(shutil.which("dbt") is None, reason="dbt not installed")
def test_shopee_profile_end_to_end_reconciles_escrow(shopee, engine, duck_path, tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_PROFILE", "shopee")
    P = PROFILES["shopee"]
    run = run_profile("shopee", "duckdb", engine=engine, duckdb_path=duck_path, now=NOW, target_path=tmp_path)
    assert run.dbt_ok, run.dbt_output
    got = {(r.rule_id, r.entity_id) for r in ExceptionStore(engine).exceptions_df(exception_class="CONFIRMED").itertuples()}
    assert got == {(m["rule_id"], m["entity_id"]) for m in shopee.dataset.manifest}              # payout formula, gross, missing escrow, missing payment
    assert all(q.passed for q in run.quality), [q for q in run.quality if not q.passed]
    import duckdb
    con = duckdb.connect(duck_path, read_only=True)
    mart = con.execute(f"select * from {P.marts}.mart_financial_reconciliation").df()
    con.close()
    want = {m["entity_id"] for m in shopee.dataset.manifest if m["rule_id"] == "SETTLE_FORMULA_MISMATCH"}
    assert set(mart.loc[mart.settlement_check == "FORMULA_MISMATCH", "order_id"]) == want        # SQL mart agrees with the Python engine
    assert set(mart.oms_check) == {"N/A"}
