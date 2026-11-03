"""End-to-end run for the Shopify profile: ingest (orders, payments, refunds) -> dbt -> reporting views -> reconcile -> monitor.

Real and simulated data stay apart: raw_shopify / platform_*_shopify / platform_reporting_shopify.
OMS, POS and settlement do not exist for a Shopify dev store, so empty stub tables keep the shared dbt models valid and the
affected checks switch to 'N/A' (dbt vars has_oms / has_settlements = false).
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.engine import Engine

from ecomplatform import db
from ecomplatform.config import entity_config, load_profile_yaml, load_sources
from ecomplatform.ingestion.core.batch_writer import BigQuerySink, DuckDBSink, Sink
from ecomplatform.ingestion.runner import RunResult, run_ingestion
from ecomplatform.monitoring.alerting import AlertManager, LarkNotifier
from ecomplatform.monitoring.freshness import check_freshness, evaluate_and_alert
from ecomplatform.monitoring.quality import CheckResult, run_quality_checks
from ecomplatform.reconciliation.engine import ReconciliationSummary, run_reconciliation
from ecomplatform.reconciliation.readers import BigQueryReader, DuckDBReader, WarehouseReader
from ecomplatform.reporting_views import duckdb_statements, render
from ecomplatform.util import utcnow

ROOT = Path(__file__).resolve().parents[2]
RAW, STUB = "raw_shopify", "raw_shopify_stub"
MARTS, REPORTING = "platform_marts_shopify", "platform_reporting_shopify"
RAW_TABLES = [f"{RAW}.orders", f"{RAW}.payments", f"{RAW}.refunds"]
ENTITIES = ["orders", "payments", "refunds"]


def dbt_vars(as_of: str) -> dict:
    return {"as_of": as_of, "raw_marketplace_schema": RAW, "raw_finance_schema": RAW, "raw_oms_schema": STUB,
            "raw_pos_schema": STUB, "platform_suffix": "_shopify", "has_oms": False, "has_settlements": False}


def create_stub_tables(sink: Sink) -> None:
    """Empty tables for the sources a Shopify store does not have, so shared dbt models compile and run."""
    cfg = load_sources()
    for dataset, source, entity in ((RAW, "finance_sim", "settlements"), (STUB, "oms_sim", "orders"), (STUB, "pos_sim", "transactions")):
        e = entity_config(cfg, source, entity)
        sink.write(dataset, e["table"], e["schema"], [])


def create_reporting_views(backend: str, *, project: str | None, location: str, duckdb_path: str | None) -> None:
    if backend == "bigquery":
        from google.cloud import bigquery
        sql = render(project, marts=MARTS, reporting=REPORTING, raw_tables=RAW_TABLES)
        bigquery.Client(project=project, location=location).query(sql).result()        # multi-statement script
    else:
        import duckdb
        sql = render("local", marts=MARTS, reporting=REPORTING, raw_tables=RAW_TABLES)
        con = duckdb.connect(duckdb_path)
        try:
            con.execute(f"create schema if not exists {REPORTING}")
            for st in duckdb_statements(sql, "local"):
                con.execute(st)
        finally:
            con.close()


def run_dbt(backend: str, *, project: str | None, location: str, duckdb_path: str | None, as_of: str,
            target_path: Path | None = None) -> subprocess.CompletedProcess:
    import json
    cmd = ["dbt", "build", "--select", "path:models/platform", "--target", "bq_oauth" if backend == "bigquery" else "local",
           "--project-dir", str(ROOT / "dbt/olist"), "--profiles-dir", str(ROOT / "dbt/olist"),
           "--vars", json.dumps(dbt_vars(as_of))]
    if target_path:
        cmd += ["--target-path", str(target_path / "target"), "--log-path", str(target_path / "logs")]
    env = {**os.environ, "DUCKDB_PATH": duckdb_path or "", "GCP_PROJECT_ID": project or "", "BQ_LOCATION": location}
    return subprocess.run(cmd, env=env, capture_output=True, text=True)


@dataclass
class ShopifyRun:
    ingest: dict[str, RunResult] = field(default_factory=dict)
    dbt_ok: bool | None = None
    dbt_output: str = ""
    recon: ReconciliationSummary | None = None
    quality: list[CheckResult] = field(default_factory=list)
    stale: int = 0


def run_shopify(backend: str, *, engine: Engine, project: str | None = None, location: str = "US", duckdb_path: str | None = None,
                skip_dbt: bool = False, alerts: AlertManager | None = None, now=None, target_path: Path | None = None) -> ShopifyRun:
    os.environ["PLATFORM_PROFILE"] = "shopify"
    sink: Sink = BigQuerySink(project, location) if backend == "bigquery" else DuckDBSink(duckdb_path)
    reader: WarehouseReader = BigQueryReader(project, location) if backend == "bigquery" else DuckDBReader(duckdb_path)
    alerts = alerts or AlertManager(engine, LarkNotifier())
    out = ShopifyRun()
    db.init_db(engine)
    create_stub_tables(sink)
    for entity in ENTITIES:
        out.ingest[entity] = run_ingestion("shopify", entity, engine=engine, sink=sink, alerts=alerts, now=now)
    as_of = (now or utcnow()).strftime("%Y-%m-%d %H:%M:%S")
    if not skip_dbt:
        res = run_dbt(backend, project=project, location=location, duckdb_path=duckdb_path, as_of=as_of, target_path=target_path)
        out.dbt_ok, out.dbt_output = res.returncode == 0, res.stdout[-4000:] + res.stderr[-1500:]
        if not out.dbt_ok:
            return out
        create_reporting_views(backend, project=project, location=location, duckdb_path=duckdb_path)
    out.recon = run_reconciliation(reader, engine, load_profile_yaml("reconciliation_rules.yaml"), as_of=now, alerts=alerts)
    out.stale = evaluate_and_alert(check_freshness(engine, load_profile_yaml("freshness.yaml")), alerts)
    out.quality = run_quality_checks(reader, engine, load_profile_yaml("quality.yaml"), alerts=alerts)
    return out
