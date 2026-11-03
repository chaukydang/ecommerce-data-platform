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


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """One simulator + one DuckDB warehouse (ingest + dbt) shared by all dashboard tests in this module."""
    from types import SimpleNamespace

    from conftest import TOKEN, SimServer
    from ecomplatform import db
    from ecomplatform.ingestion.core.retry_policy import RetryPolicy
    from ecomplatform.simulators.generator import generate

    tmp = tmp_path_factory.mktemp("dash")
    mp = pytest.MonkeyPatch()
    sim = SimServer(generate(n_orders=300, seed=7, as_of=AS_OF)).start()
    for env in ("MARKETPLACE", "OMS", "POS", "FINANCE"):
        mp.setenv(f"{env}_API_URL", sim.url)
        mp.setenv(f"{env}_API_TOKEN", TOKEN)
    engine = db.init_db(db.get_engine(f"sqlite:///{tmp / 'p.db'}"))
    path = str(tmp / "wh.duckdb")
    sink, cfgs = DuckDBSink(path), load_sources()
    retry = RetryPolicy(max_attempts=3, base_delay=0, jitter=0, sleep=lambda s: None)
    for src, ent in [("marketplace_sim", "orders"), ("oms_sim", "orders"), ("pos_sim", "transactions"),
                     ("finance_sim", "payments"), ("finance_sim", "refunds"), ("finance_sim", "settlements")]:
        run_ingestion(src, ent, engine=engine, sink=sink, now=NOW, connector=build_connector(entity_config(cfgs, src, ent), retry=retry))
    res = subprocess.run(
        ["dbt", "build", "--select", "path:models/platform", "--target", "local", "--project-dir", str(ROOT / "dbt/olist"),
         "--profiles-dir", str(ROOT / "dbt/olist"), "--target-path", str(tmp / "t"), "--log-path", str(tmp / "l"),
         "--vars", f'{{as_of: "{NOW:%Y-%m-%d %H:%M:%S}"}}'],
        env={**os.environ, "DUCKDB_PATH": path}, capture_output=True, text=True)
    assert res.returncode == 0, res.stdout[-2000:]
    yield SimpleNamespace(path=path, dataset=sim.dataset)
    sim.stop()
    mp.undo()


APP = str(ROOT / "streamlit_app/platform_dashboard.py")


def _run(monkeypatch, warehouse, page="overview", lang="vi"):
    monkeypatch.setenv("DASH_DUCKDB_PATH", warehouse)
    monkeypatch.setenv("DASH_PAGE", page)
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    at = st_testing.AppTest.from_file(APP, default_timeout=90)
    at.session_state["lang"] = lang
    return at.run()


def _html(at) -> str:
    return " ".join(m.value for m in at.markdown)


@pytest.mark.parametrize("page", ["overview", "sales", "recon", "pipeline", "guide", "settings"])
@pytest.mark.parametrize("lang", ["vi", "en"])
def test_every_page_renders_in_both_languages(built, monkeypatch, page, lang):
    at = _run(monkeypatch, built.path, page, lang)
    assert not at.exception, [e.value for e in at.exception]
    assert not at.error, [e.value for e in at.error]
    assert len(_html(at)) > 200


def _words(html: str) -> int:
    import re
    html = re.sub(r"<style>.*?</style>", " ", html, flags=re.S)
    return len(re.sub(r"<[^>]+>", " ", html).split())


def test_overview_tells_the_story_with_data_not_paragraphs(built, monkeypatch):
    html = _html(_run(monkeypatch, built.path, "overview", "en"))
    assert "of orders match" in html and "need review" in html
    assert f"{len(built.dataset.marketplace_orders):,}" in html           # order count comes from the data
    assert "24" in html                                                    # 30 injected - 6 duplicate-key findings (Python engine only)
    assert 'class="hbar"' in html and html.count("<svg") >= 4               # sparklines + activity chart
    for section in ("Needs attention", "Healthiest checks", "Channel leaderboard", "Biggest open findings", "Simulated data"):
        assert section in html
    assert "What happened" not in html and "<p>" not in html                # no walls of text
    assert _words(html) < 330, _words(html)


def test_overview_vietnamese_has_no_raw_codes(built, monkeypatch):
    html = _html(_run(monkeypatch, built.path, "overview", "vi"))
    assert "Sức khỏe đơn hàng" in html and "đơn khớp tiền" in html and "Dữ liệu mô phỏng" in html
    for code in ("AMOUNT_MISMATCH", "REFUND_EXCEEDS_PAID", "PENDING_SYNC", "FORMULA_MISMATCH"):
        assert code not in html                                             # technical codes are translated for lay readers


def test_light_theme_is_forced():
    import tomllib
    cfg = tomllib.loads((ROOT / ".streamlit/config.toml").read_text())
    assert cfg["theme"]["base"] == "light"


def test_filters_narrow_the_numbers(built, monkeypatch):
    at = _run(monkeypatch, built.path, "sales", "en")
    before = _html(at)
    at.session_state["sales_ch"] = "shopify_sim"            # (AppTest.select() double-applies format_func)
    at.run()
    assert not at.exception
    after = _html(at)
    assert after != before and "shopify_sim" in after


def test_setup_help_when_views_missing(tmp_path, monkeypatch):
    import duckdb
    p = str(tmp_path / "empty.duckdb")
    duckdb.connect(p).close()
    at = _run(monkeypatch, p, "overview", "en")
    assert any("Could not load the reporting views" in e.value for e in at.error)
    assert not at.exception
