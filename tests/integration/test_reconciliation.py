from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from conftest import AS_OF
from ecomplatform import db
from ecomplatform.config import load_yaml
from ecomplatform.ingestion.core.batch_writer import DuckDBSink
from ecomplatform.ingestion.runner import run_ingestion
from ecomplatform.monitoring.alerting import AlertManager
from ecomplatform.reconciliation.engine import run_reconciliation
from ecomplatform.reconciliation.readers import DuckDBReader
from ecomplatform.reconciliation.store import ExceptionStore

pytestmark = pytest.mark.integration
NOW = AS_OF + timedelta(hours=1)


@pytest.fixture()
def loaded(sim, engine, duck_path, fast_retry):
    from ecomplatform.config import entity_config, load_sources
    from ecomplatform.ingestion.connectors import build_connector
    sink = DuckDBSink(duck_path)
    cfgs = load_sources()
    for src, ent in [("marketplace_sim", "orders"), ("oms_sim", "orders"), ("finance_sim", "payments"),
                     ("finance_sim", "refunds"), ("finance_sim", "settlements")]:
        conn = build_connector(entity_config(cfgs, src, ent), retry=fast_retry)
        run_ingestion(src, ent, engine=engine, sink=sink, connector=conn, now=NOW)
    return DuckDBReader(duck_path)


def _confirmed(engine):
    df = ExceptionStore(engine).exceptions_df(status=("OPEN", "INVESTIGATING"), exception_class="CONFIRMED")
    return {(r.rule_id, r.entity_id) for r in df.itertuples()}


def test_rules_detect_exactly_the_injected_discrepancies(sim, engine, loaded):
    rules = load_yaml("reconciliation_rules.yaml")
    res = run_reconciliation(loaded, engine, rules, as_of=NOW)
    assert _confirmed(engine) == sim.dataset.injected()          # nothing missed, nothing spurious
    assert res.timing > 0                                        # fresh orders / unsettled orders are timing, not errors
    assert 0 < res.matched_pct < 100


def test_timing_differences_are_not_confirmed_mismatches(sim, engine, loaded):
    run_reconciliation(loaded, engine, load_yaml("reconciliation_rules.yaml"), as_of=NOW)
    t = ExceptionStore(engine).exceptions_df(exception_class="TIMING")
    fresh = t[t.entity_id.str.startswith("MP-FRESH")]
    assert {"ORD_MISSING_IN_OMS", "PAY_MISSING"} <= set(fresh.rule_id)
    assert "SETTLE_MISSING" in set(t.rule_id)                    # completed but inside the 7-day settlement window
    assert not ({(r.rule_id, r.entity_id) for r in t.itertuples()} & sim.dataset.injected())


def test_repeated_runs_do_not_duplicate_exceptions(sim, engine, loaded):
    rules = load_yaml("reconciliation_rules.yaml")
    run_reconciliation(loaded, engine, rules, as_of=NOW)
    with engine.connect() as c:
        n1 = len(c.execute(select(db.reconciliation_exceptions)).all())
        h1 = len(c.execute(select(db.reconciliation_exception_history)).all())
    r2 = run_reconciliation(loaded, engine, rules, as_of=NOW)
    with engine.connect() as c:
        assert len(c.execute(select(db.reconciliation_exceptions)).all()) == n1
        assert len(c.execute(select(db.reconciliation_exception_history)).all()) == h1   # no noise either
    assert r2.store_stats["new"] == 0 and r2.store_stats["auto_resolved"] == 0


def test_lifecycle_resolve_accept_reopen_and_audit_trail(sim, engine, loaded, duck_path, fast_retry):
    rules = load_yaml("reconciliation_rules.yaml")
    run_reconciliation(loaded, engine, rules, as_of=NOW)
    store = ExceptionStore(engine)
    df = store.exceptions_df()
    missing_pay = df[df.rule_id == "PAY_MISSING"]
    target = missing_pay[missing_pay.exception_class == "CONFIRMED"].iloc[0]
    accepted = df[(df.rule_id == "ORD_AMOUNT_MISMATCH") & (df.exception_class == "CONFIRMED")].iloc[0]
    store.set_status(target.exception_id, "INVESTIGATING", note="asked finance")
    store.set_status(accepted.exception_id, "ACCEPTED_EXCEPTION", note="known rounding agreement")

    # the missing payment arrives at the source -> next run auto-resolves it
    from ecomplatform.config import entity_config, load_sources
    from ecomplatform.ingestion.connectors import build_connector
    order = next(o for o in sim.dataset.marketplace_orders if o["order_id"] == target.entity_id)
    amt = order["total_amount_minor"] / 100
    sim.dataset.payments.append({"payment_id": "PAYID-LATE", "order_id": target.entity_id, "payment_ref": "PAY-LATE",
                                 "amount": amt, "currency": "USD", "paid_at": "2026-11-03T09:30:00Z",
                                 "updated_at": "2026-11-03T09:30:00Z"})
    cfg = entity_config(load_sources(), "finance_sim", "payments")
    run_ingestion("finance_sim", "payments", engine=engine, sink=DuckDBSink(duck_path), now=NOW,
                  connector=build_connector(cfg, retry=fast_retry))
    run_reconciliation(loaded, engine, rules, as_of=NOW)
    after = store.exceptions_df().set_index("exception_id")
    assert after.loc[target.exception_id, "exception_status"] == "RESOLVED"
    assert after.loc[target.exception_id, "resolved_at"] is not None
    assert after.loc[accepted.exception_id, "exception_status"] == "ACCEPTED_EXCEPTION"   # human decision preserved
    with engine.connect() as c:
        events = [r.event for r in c.execute(select(db.reconciliation_exception_history).where(
            db.reconciliation_exception_history.c.exception_id == target.exception_id)).all()]
    assert events == ["DETECTED", "STATUS_CHANGED", "AUTO_RESOLVED"]

    # the problem comes back (payment reversed) -> reopened, history retained
    sim.dataset.payments = [p for p in sim.dataset.payments if p["payment_id"] != "PAYID-LATE"]
    from ecomplatform.ingestion.core.batch_writer import DuckDBSink as S
    con = S(duck_path)._con(); con.execute("delete from raw_finance.payments where payment_id='PAYID-LATE'"); con.close()
    run_reconciliation(loaded, engine, rules, as_of=NOW)
    again = store.exceptions_df().set_index("exception_id")
    assert again.loc[target.exception_id, "exception_status"] == "OPEN"


def test_unresolved_amount_and_alerting_dedup(sim, engine, loaded, notifier):
    rules = load_yaml("reconciliation_rules.yaml")
    alerts = AlertManager(engine, notifier, cooldown_minutes=60)
    run_reconciliation(loaded, engine, rules, as_of=NOW, alerts=alerts)
    run_reconciliation(loaded, engine, rules, as_of=NOW, alerts=alerts)     # same incident again
    msgs = [m for m in notifier.messages if "RECONCILIATION_DISCREPANCY" in m]
    assert len(msgs) == 1                                                  # grouped, not repeated
    assert "SETTLE_FORMULA_MISMATCH" in msgs[0] or "PAY_MISSING" in msgs[0]
    assert ExceptionStore(engine).unresolved_confirmed_amount() > 0
