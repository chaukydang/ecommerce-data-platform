"""The glue Airflow calls (ecomplatform.orchestration) works end-to-end with the duckdb sink."""
from __future__ import annotations

from datetime import datetime

import pytest

from ecomplatform import orchestration as orch

pytestmark = pytest.mark.integration


@pytest.fixture()
def env(sim, tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_SINK", "duckdb")
    monkeypatch.setenv("DUCKDB_PATH", str(tmp_path / "wh.duckdb"))
    monkeypatch.setenv("PLATFORM_DB_URL", f"sqlite:///{tmp_path / 'p.db'}")
    monkeypatch.delenv("LARK_WEBHOOK_URL", raising=False)


def test_ingest_reconcile_monitor_tasks(env):
    for src, ent in [("marketplace_sim", "orders"), ("oms_sim", "orders"), ("pos_sim", "transactions"),
                     ("finance_sim", "payments"), ("finance_sim", "refunds"), ("finance_sim", "settlements")]:
        out = orch.ingest(src, ent)
        assert out["status"] == "SUCCESS" and out["loaded"] > 0
    rec = orch.reconcile(datetime(2026, 11, 3, 10, 0, 0))
    assert rec["confirmed"] == 30 and rec["matched_pct"] < 100
    mon = orch.monitor()
    assert mon["freshness_violations"] == 0 and mon["dq_failed"] == 0


def test_failure_callback_never_raises(env):
    class TI:
        dag_id, task_id, try_number = "d", "t", 2
    orch.airflow_failure_callback({"task_instance": TI(), "exception": RuntimeError("Bearer SECRETVAL boom"), "run_id": "r"})
    orch.airflow_failure_callback({})        # malformed context must be swallowed
