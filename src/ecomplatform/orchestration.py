"""Glue between Airflow (or any scheduler / CLI) and the platform packages.

Environment
  PLATFORM_DB_URL      operational metadata DB (PostgreSQL in compose; SQLite fallback)
  PLATFORM_SINK        'bigquery' (default in Airflow) | 'duckdb'
  GCP_PROJECT_ID, BQ_LOCATION   BigQuery settings;  BQ_DEDUPE=select (default, no DML, works in the free sandbox) | merge
  DUCKDB_PATH          warehouse file for the duckdb sink
  LARK_WEBHOOK_URL / LARK_WEBHOOK_SECRET   alert delivery (dry-run to logs when unset)
"""
from __future__ import annotations

import logging
import os
from datetime import datetime

from ecomplatform import db
from ecomplatform.config import load_sources, load_yaml
from ecomplatform.ingestion.core.batch_writer import BigQuerySink, DuckDBSink, Sink
from ecomplatform.ingestion.runner import RunResult, run_ingestion
from ecomplatform.monitoring.alerting import Alert, AlertManager, LarkNotifier
from ecomplatform.monitoring.freshness import check_freshness, evaluate_and_alert
from ecomplatform.monitoring.quality import run_quality_checks
from ecomplatform.reconciliation.engine import ReconciliationSummary, run_reconciliation
from ecomplatform.reconciliation.readers import BigQueryReader, DuckDBReader, WarehouseReader
from ecomplatform.util import redact

log = logging.getLogger(__name__)


def get_engine():
    return db.init_db(db.get_engine())


def get_sink() -> Sink:
    if os.getenv("PLATFORM_SINK", "bigquery") == "duckdb":
        return DuckDBSink(os.getenv("DUCKDB_PATH", "warehouse.duckdb"))
    return BigQuerySink(os.environ["GCP_PROJECT_ID"], os.getenv("BQ_LOCATION", "us-east1"), dedupe=os.getenv("BQ_DEDUPE", "select"))


def get_reader() -> WarehouseReader:
    if os.getenv("PLATFORM_SINK", "bigquery") == "duckdb":
        return DuckDBReader(os.getenv("DUCKDB_PATH", "warehouse.duckdb"))
    return BigQueryReader(os.environ["GCP_PROJECT_ID"], os.getenv("BQ_LOCATION", "us-east1"))


def get_alerts(engine=None) -> AlertManager:
    return AlertManager(engine or get_engine(), LarkNotifier(),
                        cooldown_minutes=int(os.getenv("ALERT_COOLDOWN_MINUTES", "60")))


# ---- task callables -------------------------------------------------------------------------
def ingest(source: str, entity: str) -> dict:
    engine = get_engine()
    r: RunResult = run_ingestion(source, entity, engine=engine, sink=get_sink(), sources_cfg=load_sources(),
                                 alerts=get_alerts(engine))
    return {"run_id": r.run_id, "status": r.status, "extracted": r.extracted, "loaded": r.loaded,
            "rejected": r.rejected, "retries": r.retries}


def reconcile(as_of: datetime | None = None) -> dict:
    engine = get_engine()
    s: ReconciliationSummary = run_reconciliation(get_reader(), engine, load_yaml("reconciliation_rules.yaml"),
                                                  as_of=as_of, alerts=get_alerts(engine))
    return {"run_id": s.run_id, "matched_pct": s.matched_pct, "confirmed": s.confirmed, "timing": s.timing,
            "confirmed_difference": s.confirmed_difference, **s.store_stats}


def monitor() -> dict:
    engine = get_engine()
    alerts = get_alerts(engine)
    stale = evaluate_and_alert(check_freshness(engine, load_yaml("freshness.yaml")), alerts)
    results = run_quality_checks(get_reader(), engine, load_yaml("quality.yaml"), alerts=alerts)
    failed = [r for r in results if not r.passed]
    return {"freshness_violations": stale, "dq_checks": len(results), "dq_failed": len(failed)}


def airflow_failure_callback(context: dict) -> None:
    """on_failure_callback for any Airflow task (covers failures outside the ingestion runner, e.g. dbt)."""
    try:
        ti = context["task_instance"]
        exc = context.get("exception")
        alert = Alert(category="PIPELINE_FAILURE", severity="CRITICAL", pipeline=f"{ti.dag_id}.{ti.task_id}",
                      source="airflow", issue="Airflow task failed", run_id=str(context.get("run_id", "")),
                      dataset=ti.dag_id, details={"Error": redact(f"{type(exc).__name__}: {exc}")[:500],
                                                   "Try": str(ti.try_number)},
                      action="Open the task log in the Airflow UI.")
        get_alerts().raise_alert(alert)
    except Exception:           # never let alerting break the scheduler
        log.exception("failure callback could not send alert")
