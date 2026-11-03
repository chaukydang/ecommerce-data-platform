"""Omni profile (SIMULATED multi-channel demo): ingestion -> dbt -> reconciliation -> reporting views, on DuckDB."""
from __future__ import annotations

import shutil

import pytest

from ecomplatform.ingestion.connectors import build_connector
from ecomplatform.ingestion.core.batch_writer import DuckDBSink
from ecomplatform.ingestion.runner import run_ingestion
from ecomplatform.profile_pipeline import PROFILES, run_profile
from ecomplatform.reconciliation.store import ExceptionStore

pytestmark = pytest.mark.integration
P = PROFILES["omni"]
CHANNEL_NAMES = {"Shopee", "TikTok Shop", "Lazada", "Website", "POS (stores)"}


def test_omni_ingestion_is_multi_channel_and_idempotent(engine, duck_path, monkeypatch):
    from ecomplatform.config import entity_config, load_sources
    monkeypatch.setenv("OMNI_AS_OF", "2026-11-03T17:00:00")
    sink = DuckDBSink(duck_path)
    res = {}
    for entity in ("orders", "payments", "refunds"):
        res[entity] = run_ingestion("omni", entity, engine=engine, sink=sink,
                                    connector=build_connector(entity_config(load_sources(), "omni", entity)))
        assert res[entity].status == "SUCCESS" and res[entity].rejected == 0 and res[entity].loaded > 1000
    df = sink.query_df("select channel, count(*) n, sum(total_amount) s from raw_omni.orders group by 1")
    assert set(df.channel) == CHANNEL_NAMES and (df.n > 5000).all()
    cur = sink.query_df("select distinct currency from raw_omni.orders")
    assert list(cur.currency) == ["VND"]
    again = run_ingestion("omni", "orders", engine=engine, sink=sink, connector=build_connector(entity_config(load_sources(), "omni", "orders")))
    assert again.loaded == 0


@pytest.mark.dbt
@pytest.mark.skipif(shutil.which("dbt") is None, reason="dbt not installed")
def test_omni_profile_end_to_end(engine, duck_path, tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_PROFILE", "omni")                    # run_profile sets it globally: let monkeypatch restore it
    monkeypatch.setenv("OMNI_AS_OF", "2026-11-03T17:00:00")
    run = run_profile("omni", "duckdb", engine=engine, duckdb_path=duck_path, target_path=tmp_path)
    assert run.dbt_ok, run.dbt_output
    assert all(q.passed for q in run.quality), [q for q in run.quality if not q.passed]
    got = ExceptionStore(engine).exceptions_df(exception_class="CONFIRMED")
    assert 0 < len(got) < 400                                          # planted problems exist but the data is overwhelmingly clean
    assert {r for r in got.rule_id} <= {"PAY_MISSING", "PAY_AMOUNT_MISMATCH", "PAY_CURRENCY_MISMATCH", "PAY_DUPLICATE_REF", "REF_EXCEEDS_PAID"}
    import duckdb
    con = duckdb.connect(duck_path, read_only=True)
    ch = {r[0] for r in con.execute(f"select distinct channel from {P.reporting}.v_sales_daily").fetchall()}
    refunded = con.execute(f"select sum(refunded_amount) from {P.reporting}.v_sales_daily").fetchone()[0]
    con.close()
    assert ch == CHANNEL_NAMES and refunded > 0
