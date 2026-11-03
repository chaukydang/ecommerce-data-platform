"""Olist profile: CSV files in the Kaggle layout -> platform (DuckDB) -> dbt -> reconciliation -> dashboard."""
from __future__ import annotations

import shutil

import pytest

from ecomplatform.ingestion.connectors import build_connector
from ecomplatform.ingestion.core.batch_writer import DuckDBSink
from ecomplatform.ingestion.core.retry_policy import PermanentError
from ecomplatform.ingestion.runner import run_ingestion
from ecomplatform.profile_pipeline import PROFILES, run_profile
from ecomplatform.reconciliation.store import ExceptionStore
from olist_fixture import write_olist_fixture

pytestmark = pytest.mark.integration
P = PROFILES["olist"]


@pytest.fixture()
def olist(tmp_path, monkeypatch):
    info = write_olist_fixture(tmp_path / "olist_csv")
    monkeypatch.setenv("OLIST_DATA_DIR", str(tmp_path / "olist_csv"))
    return info


def test_olist_ingestion_maps_orders_and_payments(olist, engine, duck_path):
    from ecomplatform.config import entity_config, load_sources
    sink = DuckDBSink(duck_path)
    for entity in ("orders", "payments"):
        r = run_ingestion("olist", entity, engine=engine, sink=sink,
                          connector=build_connector(entity_config(load_sources(), "olist", entity)))
        assert r.status == "SUCCESS" and r.rejected == 0
        assert r.loaded == (olist["n_orders"] if entity == "orders" else olist["n_payments"])      # historical 2017 data is reached
    df = sink.query_df("select * from raw_olist.orders")
    assert set(df.status) <= {"PENDING", "PAID", "SHIPPED", "COMPLETED", "CANCELLED"} and set(df.currency) == {"BRL"}
    canceled = [o for o, s in olist["statuses"].items() if s in ("canceled", "unavailable")]
    assert (df[df.order_id.isin(canceled)].total_amount == 0).all() and (df.status[df.order_id.isin(canceled)] == "CANCELLED").all()
    again = run_ingestion("olist", "orders", engine=engine, sink=sink, connector=build_connector(entity_config(load_sources(), "olist", "orders")))
    assert again.loaded == 0                                                                       # idempotent


def test_missing_data_dir_gives_a_clear_error(monkeypatch, engine, duck_path):
    from ecomplatform.config import entity_config, load_sources
    monkeypatch.delenv("OLIST_DATA_DIR", raising=False)
    with pytest.raises(PermanentError, match="OLIST_DATA_DIR"):
        run_ingestion("olist", "orders", engine=engine, sink=DuckDBSink(duck_path),
                      connector=build_connector(entity_config(load_sources(), "olist", "orders")))


@pytest.mark.dbt
@pytest.mark.skipif(shutil.which("dbt") is None, reason="dbt not installed")
def test_olist_profile_end_to_end_finds_planted_payment_problems(olist, engine, duck_path, tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_PROFILE", "olist")
    run = run_profile("olist", "duckdb", engine=engine, duckdb_path=duck_path, target_path=tmp_path)
    assert run.dbt_ok, run.dbt_output
    got = {(r.rule_id, r.entity_id) for r in ExceptionStore(engine).exceptions_df(exception_class="CONFIRMED").itertuples()}
    want = {(rule, oid) for rule, ids in olist["manifest"].items() for oid in ids}
    assert got == want                                    # split (voucher + card) payments are NOT false positives
    assert all(q.passed for q in run.quality), [q for q in run.quality if not q.passed]
    assert not any(k.startswith(("ORD_", "SETTLE_", "OMS_", "REF_")) for k in run.recon.per_rule)

    import duckdb
    con = duckdb.connect(duck_path, read_only=True)
    n = con.execute(f"select count(*) from {P.marts}.fct_platform_orders").fetchone()[0]
    dates = con.execute(f"select min(order_date), max(order_date) from {P.marts}.mart_daily_sales").fetchone()
    con.close()
    assert n == olist["n_orders"] and dates[0].year == 2017                                    # real-period dates, not 'now'
