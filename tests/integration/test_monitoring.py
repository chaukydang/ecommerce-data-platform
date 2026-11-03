from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from conftest import AS_OF
from ecomplatform import db
from ecomplatform.config import entity_config, load_sources, load_yaml
from ecomplatform.ingestion.connectors import build_connector
from ecomplatform.ingestion.core.batch_writer import DuckDBSink
from ecomplatform.ingestion.runner import run_ingestion
from ecomplatform.monitoring.alerting import AlertManager
from ecomplatform.monitoring.freshness import check_freshness, evaluate_and_alert
from ecomplatform.monitoring.quality import run_quality_checks
from ecomplatform.monitoring.reliability import reliability_summary
from ecomplatform.reconciliation.readers import DuckDBReader

pytestmark = pytest.mark.integration
NOW = AS_OF + timedelta(hours=1)


def _ingest_all(engine, sink, fast_retry, now=NOW):
    cfgs = load_sources()
    for src, ent in [("marketplace_sim", "orders"), ("oms_sim", "orders"), ("pos_sim", "transactions"),
                     ("finance_sim", "payments"), ("finance_sim", "refunds"), ("finance_sim", "settlements")]:
        run_ingestion(src, ent, engine=engine, sink=sink, now=now,
                      connector=build_connector(entity_config(cfgs, src, ent), retry=fast_retry))


def test_freshness_violation_alert_grouping_and_recovery(sim, engine, duck_path, fast_retry, notifier):
    from ecomplatform.util import utcnow
    sink = DuckDBSink(duck_path)
    cfg = load_yaml("freshness.yaml")
    alerts = AlertManager(engine, notifier, cooldown_minutes=60)

    # nothing ever loaded -> every dataset violates
    res = check_freshness(engine, cfg, now=utcnow())
    assert all(r.violated for r in res) and evaluate_and_alert(res, alerts) == len(cfg["datasets"])

    _ingest_all(engine, sink, fast_retry)
    fresh = check_freshness(engine, cfg, now=utcnow() + timedelta(minutes=5))
    assert not any(r.violated for r in fresh)
    assert evaluate_and_alert(fresh, alerts) == 0
    assert sum("RECOVERED" in m for m in notifier.messages) == len(cfg["datasets"])

    # 75 minutes later the 30-minute datasets are stale again; repeated checks are grouped into one alert
    late = utcnow() + timedelta(minutes=75)
    before = len(notifier.messages)
    for k in range(3):
        evaluate_and_alert(check_freshness(engine, cfg, now=late + timedelta(minutes=k)), alerts, now=late + timedelta(minutes=k))
    stale = [m for m in notifier.messages[before:] if "FRESHNESS_VIOLATION" in m and "raw_marketplace.orders" in m]
    assert len(stale) == 1 and "Current Delay" in stale[0] and "Last Successful Load" in stale[0]


def test_quality_checks_pass_on_clean_data_and_flag_problems(sim, engine, duck_path, fast_retry, notifier):
    sink = DuckDBSink(duck_path)
    _ingest_all(engine, sink, fast_retry)
    cfg = load_yaml("quality.yaml")
    alerts = AlertManager(engine, notifier)
    results = run_quality_checks(DuckDBReader(duck_path), engine, cfg, alerts=alerts)
    assert not [r for r in results if not r.passed], [r for r in results if not r.passed]
    with engine.connect() as c:
        assert len(c.execute(select(db.data_quality_results)).all()) == len(results)

    # corrupt: orphan payment + invalid status + schema drift column
    con = sink._con()
    con.execute("update raw_finance.payments set order_id='MP-DOES-NOT-EXIST' where payment_id='PAYID-000001'")
    con.execute("update raw_marketplace.orders set status='WEIRD' where order_id='MP-000003'")
    con.execute("alter table raw_oms.orders add column surprise VARCHAR")
    con.close()
    bad = {(r.dataset, r.check) for r in run_quality_checks(DuckDBReader(duck_path), engine, cfg, alerts=alerts) if not r.passed}
    assert ("raw_finance.payments", "relationship:order_id->raw_marketplace.orders") in bad
    assert ("raw_marketplace.orders", "accepted_values:status") in bad
    assert ("raw_oms.orders", "schema_drift") in bad
    assert any("DATA_QUALITY_FAILURE" in m for m in notifier.messages)


def test_reliability_summary_measures_recovery_time(sim, engine, duck_path, fast_retry):
    sink = DuckDBSink(duck_path)
    retry = type(fast_retry)(max_attempts=1, base_delay=0, jitter=0, sleep=lambda s: None)
    cfg = entity_config(load_sources(), "marketplace_sim", "orders")
    sim.faults.fail_from_call = 1
    with pytest.raises(Exception):
        run_ingestion("marketplace_sim", "orders", engine=engine, sink=sink, now=NOW,
                      connector=build_connector(cfg, retry=retry))
    sim.faults.reset()
    run_ingestion("marketplace_sim", "orders", engine=engine, sink=sink, now=NOW,
                  connector=build_connector(cfg, retry=retry))
    s = reliability_summary(engine).set_index("pipeline_name").loc["ingest_marketplace_sim_orders"]
    assert s.runs == 2 and s.failed_runs == 1 and s.success_rate_pct == 50.0
    assert s.mean_recovery_minutes is not None and not s.open_outage
