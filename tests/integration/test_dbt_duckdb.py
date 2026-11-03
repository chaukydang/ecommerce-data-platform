"""Runs the platform dbt models on DuckDB and proves the SQL reconciliation mart agrees with the Python rules."""
from __future__ import annotations

import os
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path

import duckdb
import pytest

from conftest import AS_OF
from ecomplatform.config import entity_config, load_sources, load_yaml
from ecomplatform.ingestion.connectors import build_connector
from ecomplatform.ingestion.core.batch_writer import DuckDBSink
from ecomplatform.ingestion.runner import run_ingestion
from ecomplatform.reconciliation.engine import run_reconciliation
from ecomplatform.reconciliation.readers import DuckDBReader
from ecomplatform.reconciliation.store import ExceptionStore

ROOT = Path(__file__).resolve().parents[2]
NOW = AS_OF + timedelta(hours=1)
pytestmark = [pytest.mark.integration, pytest.mark.dbt,
              pytest.mark.skipif(shutil.which("dbt") is None, reason="dbt not installed")]

# python rule id -> (mart column, value) : both sides must flag exactly the same orders
EQUIVALENT = {
    "ORD_MISSING_IN_OMS": ("oms_check", "MISSING_IN_OMS"), "ORD_AMOUNT_MISMATCH": ("oms_check", "AMOUNT_MISMATCH"),
    "PAY_MISSING": ("payment_check", "MISSING"), "PAY_AMOUNT_MISMATCH": ("payment_check", "AMOUNT_MISMATCH"),
    "PAY_CURRENCY_MISMATCH": ("currency_check", "CURRENCY_MISMATCH"),
    "REF_WITHOUT_PAYMENT": ("refund_check", "REFUND_WITHOUT_PAYMENT"), "REF_EXCEEDS_PAID": ("refund_check", "REFUND_EXCEEDS_PAID"),
    "SETTLE_MISSING": ("settlement_check", "MISSING"), "SETTLE_FORMULA_MISMATCH": ("settlement_check", "FORMULA_MISMATCH"),
}


def test_dbt_build_and_sql_mart_matches_python_rules(sim, engine, duck_path, fast_retry, tmp_path):
    sink, cfgs = DuckDBSink(duck_path), load_sources()
    for src, ent in [("marketplace_sim", "orders"), ("oms_sim", "orders"), ("pos_sim", "transactions"),
                     ("finance_sim", "payments"), ("finance_sim", "refunds"), ("finance_sim", "settlements")]:
        run_ingestion(src, ent, engine=engine, sink=sink, now=NOW,
                      connector=build_connector(entity_config(cfgs, src, ent), retry=fast_retry))

    env = {**os.environ, "DUCKDB_PATH": duck_path}
    cmd = ["dbt", "build", "--select", "path:models/platform", "--target", "local",
           "--project-dir", str(ROOT / "dbt/olist"), "--profiles-dir", str(ROOT / "dbt/olist"),
           "--target-path", str(tmp_path / "target"), "--log-path", str(tmp_path / "logs"),
           "--vars", f'{{as_of: "{NOW:%Y-%m-%d %H:%M:%S}"}}']
    res = subprocess.run(cmd, env=env, capture_output=True, text=True)
    assert res.returncode == 0, res.stdout[-4000:] + res.stderr[-1000:]
    assert "ERROR=0" in res.stdout.replace(" ", ""), res.stdout[-800:]   # WARNs = the 3 injected duplicate-key findings

    con = duckdb.connect(duck_path, read_only=True)
    mart = con.execute("select * from platform_marts.mart_financial_reconciliation").df()
    orders = con.execute("select count(*) from platform_marts.fct_platform_orders").fetchone()[0]
    daily = con.execute("select sum(orders) from platform_marts.mart_daily_sales").fetchone()[0]
    dates = con.execute("select count(*) from platform_marts.dim_date").fetchone()[0]
    con.close()
    assert orders == len(mart) == daily == len(sim.dataset.marketplace_orders)
    assert dates >= 30

    run_reconciliation(DuckDBReader(duck_path), engine, load_yaml("reconciliation_rules.yaml"), as_of=NOW)
    py = ExceptionStore(engine).exceptions_df(exception_class="CONFIRMED")
    for rule_id, (col, val) in EQUIVALENT.items():
        sql_ids = set(mart.loc[mart[col] == val, "order_id"])
        py_ids = set(py.loc[py.rule_id == rule_id, "entity_id"])
        assert sql_ids == py_ids, (rule_id, sql_ids ^ py_ids)
    # timing differences are visible in SQL too
    assert (mart.payment_check == "PENDING_SYNC").any() and (mart.settlement_check == "PENDING_SETTLEMENT").any()
