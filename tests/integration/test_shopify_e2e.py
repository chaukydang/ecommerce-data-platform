"""Shopify profile end to end (mock Admin API -> DuckDB -> dbt -> reporting views -> reconciliation -> monitoring -> dashboard)."""
from __future__ import annotations

import shutil
from datetime import timedelta
from pathlib import Path

import pytest

from conftest import AS_OF, FakeNotifier
from ecomplatform.monitoring.alerting import AlertManager
from ecomplatform.reconciliation.store import ExceptionStore
from ecomplatform.shopify_pipeline import MARTS, REPORTING, run_shopify

st_testing = pytest.importorskip("streamlit.testing.v1")
pytest.importorskip("sqlglot")
ROOT = Path(__file__).resolve().parents[2]
NOW = AS_OF + timedelta(hours=1)
pytestmark = [pytest.mark.integration, pytest.mark.dbt,
              pytest.mark.skipif(shutil.which("dbt") is None, reason="dbt not installed")]


@pytest.fixture()
def shopify_run(shopify, engine, duck_path, tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_PROFILE", "shopify")                 # restored after the test
    notifier = FakeNotifier()
    run = run_shopify("duckdb", engine=engine, duckdb_path=duck_path, now=NOW, target_path=tmp_path,
                      alerts=AlertManager(engine, notifier))
    assert run.dbt_ok, run.dbt_output
    return run, notifier


def test_pipeline_ingests_models_and_reconciles_real_shaped_data(shopify, shopify_run, engine, duck_path):
    run, _ = shopify_run
    assert {e: r.status for e, r in run.ingest.items()} == dict.fromkeys(("orders", "payments", "refunds"), "SUCCESS")
    # reconciliation finds exactly the problems planted in the mock (payment missing / half paid) and nothing spurious
    got = {(r.rule_id, r.entity_id) for r in ExceptionStore(engine).exceptions_df(exception_class="CONFIRMED").itertuples()}
    assert got == {(m["rule_id"], m["entity_id"]) for m in shopify.dataset.manifest}
    assert run.recon.checked > 0 and all(q.passed for q in run.quality), [q for q in run.quality if not q.passed]
    # OMS / settlement rules are disabled, not reported as "everything missing"
    assert not any(k.startswith(("ORD_", "SETTLE_", "OMS_")) for k in run.recon.per_rule)

    import duckdb
    con = duckdb.connect(duck_path, read_only=True)
    n_orders = con.execute(f"select count(*) from {MARTS}.fct_platform_orders").fetchone()[0]
    mart = con.execute(f"select * from {MARTS}.mart_financial_reconciliation").df()
    sales = con.execute(f"select sum(orders) from {MARTS}.mart_daily_sales").fetchone()[0]
    views = con.execute("select count(*) from information_schema.tables where table_schema = ?", [REPORTING]).fetchone()[0]
    sim_marts = con.execute("select count(*) from information_schema.tables where table_schema = 'platform_marts'").fetchone()[0]
    con.close()
    assert n_orders == sales == len(shopify.dataset.orders) and views == 7 and sim_marts == 0     # real data isolated from simulated
    assert set(mart.oms_check) == {"N/A"} and set(mart.settlement_check) == {"N/A"}
    # SQL mart agrees with the Python engine on the planted problems
    assert set(mart.loc[mart.payment_check == "MISSING", "order_id"]) == {m["entity_id"] for m in shopify.dataset.manifest if m["rule_id"] == "PAY_MISSING"}
    assert set(mart.loc[mart.payment_check == "AMOUNT_MISMATCH", "order_id"]) == {m["entity_id"] for m in shopify.dataset.manifest
                                                                                  if m["rule_id"] == "PAY_AMOUNT_MISMATCH"}


@pytest.mark.parametrize("page", ["overview", "recon", "pipeline", "guide"])
def test_dashboard_shows_shopify_data_with_real_labels(shopify_run, shopify, duck_path, monkeypatch, page):
    import streamlit as st
    st.cache_resource.clear()
    st.cache_data.clear()
    monkeypatch.setenv("DASH_DUCKDB_PATH", duck_path)
    monkeypatch.setenv("DASH_DATASET", REPORTING)
    monkeypatch.setenv("DASH_MARTS", MARTS)
    monkeypatch.setenv("DASH_SIMULATED", "0")
    monkeypatch.setenv("DASH_PAGE", page)
    at = st_testing.AppTest.from_file(str(ROOT / "streamlit_app/platform_dashboard.py"), default_timeout=90)
    at.session_state["lang"] = "en"
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    html = " ".join(m.value for m in at.markdown) + " ".join(i.value for i in at.info)
    assert "Shopify test store" in html or page == "guide"
    assert "Simulated data" not in html and "simulated" not in html.lower()
    if page == "overview":
        assert f"{len(shopify.dataset.orders)}" in html and "orders" in html
    if page == "pipeline":
        assert "orders" in html and "payments" in html and "refunds" in html
