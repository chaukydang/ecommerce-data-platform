"""Runs the Streamlit platform dashboard headlessly (streamlit AppTest) against the DuckDB warehouse."""
from __future__ import annotations

import os
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest

from conftest import AS_OF
from ecomplatform.config import entity_config, load_sources
from ecomplatform.ingestion.connectors import build_connector
from ecomplatform.ingestion.core.batch_writer import DuckDBSink
from ecomplatform.ingestion.runner import run_ingestion

st_testing = pytest.importorskip("streamlit.testing.v1")
pytest.importorskip("plotly")
pytest.importorskip("sqlglot")
ROOT = Path(__file__).resolve().parents[2]
NOW = AS_OF + timedelta(hours=1)
pytestmark = [pytest.mark.integration, pytest.mark.dbt,
              pytest.mark.skipif(shutil.which("dbt") is None, reason="dbt not installed")]


@pytest.fixture(autouse=True)
def _fresh_streamlit_caches():
    import streamlit as st
    st.cache_resource.clear()
    st.cache_data.clear()
    yield


@pytest.fixture()
def warehouse(sim, engine, duck_path, fast_retry, tmp_path):
    sink, cfgs = DuckDBSink(duck_path), load_sources()
    for src, ent in [("marketplace_sim", "orders"), ("oms_sim", "orders"), ("pos_sim", "transactions"),
                     ("finance_sim", "payments"), ("finance_sim", "refunds"), ("finance_sim", "settlements")]:
        run_ingestion(src, ent, engine=engine, sink=sink, now=NOW,
                      connector=build_connector(entity_config(cfgs, src, ent), retry=fast_retry))
    res = subprocess.run(
        ["dbt", "build", "--select", "path:models/platform", "--target", "local", "--project-dir", str(ROOT / "dbt/olist"),
         "--profiles-dir", str(ROOT / "dbt/olist"), "--target-path", str(tmp_path / "t"), "--log-path", str(tmp_path / "l"),
         "--vars", f'{{as_of: "{NOW:%Y-%m-%d %H:%M:%S}"}}'],
        env={**os.environ, "DUCKDB_PATH": duck_path}, capture_output=True, text=True)
    assert res.returncode == 0, res.stdout[-2000:]
    return duck_path


def test_dashboard_renders_with_expected_numbers(warehouse, sim, monkeypatch):
    monkeypatch.setenv("DASH_DUCKDB_PATH", warehouse)
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    at = st_testing.AppTest.from_file(str(ROOT / "streamlit_app/platform_dashboard.py"), default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    assert not at.error, [e.value for e in at.error]
    assert [t.label for t in at.tabs] == ["Sales overview", "Financial reconciliation", "Pipeline health"]
    metrics = {m.label: m.value for m in at.metric}
    for label in ("Net sales", "Gross sales", "Orders", "Cancel rate", "Clean orders", "Confirmed findings",
                  "Timing differences", "Unresolved difference", "Raw tables", "Rows loaded", "Last load (UTC)"):
        assert label in metrics, label
    assert metrics["Orders"] == f"{len(sim.dataset.marketplace_orders):,}"
    assert metrics["Raw tables"] == "6"
    # 30 injected - 6 duplicate-key findings (those exist only in the Python engine) = 24 order-level confirmed findings
    assert metrics["Confirmed findings"] == "24"


def test_dashboard_filters_work(warehouse, monkeypatch):
    monkeypatch.setenv("DASH_DUCKDB_PATH", warehouse)
    at = st_testing.AppTest.from_file(str(ROOT / "streamlit_app/platform_dashboard.py"), default_timeout=60).run()
    all_orders = int(at.metric[2].value.replace(",", ""))
    channels = at.multiselect[0]
    channels.set_value([channels.options[0]]).run()
    assert not at.exception
    assert int(at.metric[2].value.replace(",", "")) < all_orders          # channel filter narrows the KPIs


def test_dashboard_shows_setup_help_when_views_missing(tmp_path, monkeypatch):
    import duckdb
    p = str(tmp_path / "empty.duckdb")
    duckdb.connect(p).close()
    monkeypatch.setenv("DASH_DUCKDB_PATH", p)
    at = st_testing.AppTest.from_file(str(ROOT / "streamlit_app/platform_dashboard.py"), default_timeout=60).run()
    assert any("Could not load the reporting views" in e.value for e in at.error)
    assert not at.exception
