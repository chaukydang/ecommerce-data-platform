from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from conftest import AS_OF
from ecomplatform import db
from ecomplatform.config import entity_config, load_sources
from ecomplatform.ingestion.connectors import build_connector
from ecomplatform.ingestion.core.batch_writer import DuckDBSink
from ecomplatform.ingestion.core.checkpoint import CheckpointStore
from ecomplatform.ingestion.core.retry_policy import AuthError, RetryPolicy, TransientError
from ecomplatform.ingestion.runner import run_ingestion
from ecomplatform.monitoring.alerting import AlertManager

pytestmark = pytest.mark.integration
NOW = AS_OF + timedelta(hours=1)


def _count(sink, fq):
    return int(sink.query_df(f"select count(*) c from {fq}")["c"][0])


def _run(engine, sink, source, entity, retry=None, **kw):
    cfg = entity_config(load_sources(), source, entity)
    conn = build_connector(cfg, retry=retry)
    return run_ingestion(source, entity, engine=engine, sink=sink, connector=conn, now=NOW, **kw)


def test_end_to_end_marketplace_and_all_entities(sim, engine, duck_path, fast_retry):
    sink = DuckDBSink(duck_path)
    for src, ent in [("marketplace_sim", "orders"), ("oms_sim", "orders"), ("pos_sim", "transactions"),
                     ("finance_sim", "payments"), ("finance_sim", "refunds"), ("finance_sim", "settlements")]:
        r = _run(engine, sink, src, ent, retry=fast_retry)
        assert r.status == "SUCCESS" and r.extracted == r.loaded and r.extracted > 0, (src, ent, r)
    # marketplace money was converted cents -> major units
    df = sink.query_df("select order_id, total_amount, status from raw_marketplace.orders where order_id='MP-000001'")
    src = next(o for o in sim.dataset.marketplace_orders if o["order_id"] == "MP-000001")
    assert df.total_amount[0] == pytest.approx(src["total_amount_minor"] / 100)
    assert df.status[0] in {"PENDING", "PAID", "SHIPPED", "COMPLETED", "CANCELLED"}
    # every ingested row carries traceable metadata
    meta = sink.query_df("select count(*) c from raw_marketplace.orders where batch_id is null or record_hash is null "
                         "or ingested_at is null or source_system is null or source_record_id is null")
    assert meta.c[0] == 0
    # one audit row per execution
    with engine.connect() as c:
        assert len(c.execute(select(db.pipeline_run_audit)).all()) == 6


def test_rerun_identical_window_is_idempotent(sim, engine, duck_path, fast_retry):
    sink = DuckDBSink(duck_path)
    first = _run(engine, sink, "marketplace_sim", "orders", retry=fast_retry)
    n = _count(sink, "raw_marketplace.orders")
    # same window again (forced backfill from the original start) must not add rows
    cfg = entity_config(load_sources(), "marketplace_sim", "orders")
    again = run_ingestion("marketplace_sim", "orders", engine=engine, sink=sink, now=NOW,
                          window_start=NOW - timedelta(days=cfg["initial_lookback_days"]),
                          connector=build_connector(cfg, retry=fast_retry))
    assert again.extracted == first.extracted and again.loaded == 0
    assert _count(sink, "raw_marketplace.orders") == n
    # incremental run after the checkpoint (overlap window only) also adds nothing new
    third = _run(engine, sink, "marketplace_sim", "orders", retry=fast_retry)
    assert third.loaded == 0 and _count(sink, "raw_marketplace.orders") == n


def test_incremental_picks_up_updates_as_new_versions(sim, engine, duck_path, fast_retry):
    sink = DuckDBSink(duck_path)
    _run(engine, sink, "marketplace_sim", "orders", retry=fast_retry)
    n = _count(sink, "raw_marketplace.orders")
    sim.dataset.update_order_status("MP-000002", "CANCELLED", NOW - timedelta(minutes=2))
    sim.dataset.add_order("MP-NEW001", 9900, NOW - timedelta(minutes=1))
    r = _run(engine, sink, "marketplace_sim", "orders", retry=fast_retry)
    assert r.loaded == 2 and r.extracted < 50          # only the overlap window was re-read
    assert _count(sink, "raw_marketplace.orders") == n + 2
    versions = sink.query_df("select count(*) c from raw_marketplace.orders where order_id='MP-000002'")
    assert versions.c[0] == 2                          # history preserved


def test_rate_limit_and_timeouts_are_retried(sim, engine, duck_path):
    sink = DuckDBSink(duck_path)
    sim.faults.rate_limit_on = {1, 2}
    sim.faults.server_error_on = {3}
    retry = RetryPolicy(max_attempts=5, base_delay=0.0, jitter=0.0, sleep=lambda s: None)
    r = _run(engine, sink, "marketplace_sim", "orders", retry=retry)
    assert r.status == "SUCCESS" and r.retries == 3


def test_outage_mid_pagination_does_not_advance_checkpoint_and_recovers(sim, engine, duck_path, notifier):
    sink = DuckDBSink(duck_path)
    alerts = AlertManager(engine, notifier, cooldown_minutes=60)
    retry = RetryPolicy(max_attempts=2, base_delay=0.0, jitter=0.0, sleep=lambda s: None)
    # page size 200 and 300+ orders -> second page request is call #2; outage from call 2 on
    sim.faults.fail_from_call = 2
    with pytest.raises(TransientError):
        _run(engine, sink, "marketplace_sim", "orders", retry=retry, alerts=alerts)
    assert CheckpointStore(engine).get("marketplace_sim", "orders") is None          # not advanced
    committed = _count(sink, "raw_marketplace.orders")
    assert 0 < committed < 303                                                       # first page is durable
    with engine.connect() as c:
        row = c.execute(select(db.pipeline_run_audit).where(db.pipeline_run_audit.c.status == "FAILED")).mappings().one()
        assert row["error_category"] == "TRANSIENT" and row["retry_count"] >= 1
        assert c.execute(select(db.failed_batches)).first() is not None
    assert len(notifier.messages) == 1 and "PIPELINE_FAILURE" in notifier.messages[0]
    # service comes back: rerun recovers without losing or duplicating committed rows
    sim.faults.reset()
    r = _run(engine, sink, "marketplace_sim", "orders", retry=retry, alerts=alerts)
    assert r.status == "SUCCESS"
    assert _count(sink, "raw_marketplace.orders") == len(sim.dataset.marketplace_orders)
    dup = sink.query_df("select count(*) c from (select source_record_id, record_hash from raw_marketplace.orders "
                        "group by 1,2 having count(*)>1)")
    assert dup.c[0] == 0
    assert CheckpointStore(engine).get("marketplace_sim", "orders") is not None
    assert any("RECOVERED" in m for m in notifier.messages)                          # recovery visible


def test_invalid_credentials_fail_without_leaking_secret(sim, engine, duck_path, monkeypatch, notifier):
    monkeypatch.setenv("MARKETPLACE_API_TOKEN", "WRONG-SECRET-TOKEN-999")
    sink = DuckDBSink(duck_path)
    alerts = AlertManager(engine, notifier)
    with pytest.raises(AuthError):
        _run(engine, sink, "marketplace_sim", "orders", alerts=alerts)
    with engine.connect() as c:
        stored = " ".join(str(v) for row in c.execute(select(db.pipeline_run_audit)).mappings() for v in row.values())
        stored += " ".join(str(v) for row in c.execute(select(db.failed_batches)).mappings() for v in row.values())
    assert "WRONG-SECRET-TOKEN-999" not in stored and "WRONG-SECRET-TOKEN-999" not in " ".join(notifier.messages)
    assert "AUTH" in stored


def test_schema_validation_quarantines_bad_records(sim, engine, duck_path, fast_retry):
    for o in sim.dataset.marketplace_orders[:5]:
        o["total_amount_minor"] = None                      # required amount missing
    sink = DuckDBSink(duck_path)
    r = _run(engine, sink, "marketplace_sim", "orders", retry=fast_retry)
    assert r.status == "PARTIAL_SUCCESS" and r.rejected == 5
    assert _count(sink, "raw_marketplace.orders__rejected") == 5
    assert _count(sink, "raw_marketplace.orders") == len(sim.dataset.marketplace_orders) - 5


def test_too_many_rejects_fail_the_batch(sim, engine, duck_path, fast_retry):
    for o in sim.dataset.marketplace_orders[:120]:
        o["total_amount_minor"] = None
    sink = DuckDBSink(duck_path)
    with pytest.raises(Exception, match="rejected"):
        _run(engine, sink, "marketplace_sim", "orders", retry=fast_retry)
    assert CheckpointStore(engine).get("marketplace_sim", "orders") is None
