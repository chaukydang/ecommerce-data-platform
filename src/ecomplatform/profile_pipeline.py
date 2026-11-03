"""End-to-end run for a *data profile* (a real or real-shaped source): ingest -> dbt -> reporting views -> reconcile -> monitor.

Profiles keep their data apart from the simulated platform and from each other:
    raw_<p>  raw_<p>_stub  platform_marts_<p>  platform_staging_<p>  platform_reporting_<p>
Sources that do not provide a table (OMS, POS, refunds, settlements ...) get empty stub tables so the shared dbt models run, and
the checks that need them are switched off (dbt vars has_oms / has_settlements, `disable:` in configs/reconciliation_rules_<p>.yaml).
"""
from __future__ import annotations

import json
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
FINANCE_TABLES = ("payments", "refunds", "settlements")


@dataclass(frozen=True)
class Profile:
    name: str
    entities: tuple[str, ...]
    label: str                      # shown on the dashboard chip
    currency_symbol: str = "$"
    source: str | None = None       # key in sources.yaml (defaults to the profile name)
    simulated: bool = False         # dashboard shows the "simulated data" label instead of "real data"

    @property
    def src(self) -> str:
        return self.source or self.name

    raw = property(lambda s: f"raw_{s.name}")
    stub = property(lambda s: f"raw_{s.name}_stub")
    marts = property(lambda s: f"platform_marts_{s.name}")
    reporting = property(lambda s: f"platform_reporting_{s.name}")
    has_settlements = property(lambda s: "settlements" in s.entities)
    raw_tables = property(lambda s: [f"{s.raw}.{e}" for e in s.entities])


PROFILES: dict[str, Profile] = {
    "shopify": Profile("shopify", ("orders", "payments", "refunds"), "Shopify test store"),
    "shopee": Profile("shopee", ("orders", "payments", "settlements"), "Shopee Open Platform"),
    "omni": Profile("omni", ("orders", "payments", "refunds"), "Omnichannel demo (simulated)", currency_symbol="₫", simulated=True),
    "olist": Profile("olist", ("orders", "payments"), "Olist (real marketplace data)", currency_symbol="R$"),
}


def dbt_vars(p: Profile, as_of: str) -> dict:
    return {"as_of": as_of, "raw_marketplace_schema": p.raw, "raw_finance_schema": p.raw, "raw_oms_schema": p.stub,
            "raw_pos_schema": p.stub, "platform_suffix": f"_{p.name}", "has_oms": False, "has_settlements": p.has_settlements}


def create_stub_tables(sink: Sink, p: Profile) -> None:
    """Empty tables for what the source does not provide, so the shared dbt models compile and run."""
    cfg = load_sources()
    plan = [(p.raw, "finance_sim", t) for t in FINANCE_TABLES if t not in p.entities]
    plan += [(p.stub, "oms_sim", "orders"), (p.stub, "pos_sim", "transactions")]
    for dataset, source, entity in plan:
        e = entity_config(cfg, source, entity)
        sink.write(dataset, e["table"], e["schema"], [])


def create_reporting_views(p: Profile, backend: str, *, project: str | None, location: str, duckdb_path: str | None) -> None:
    if backend == "bigquery":
        from google.cloud import bigquery
        sql = render(project, marts=p.marts, reporting=p.reporting, raw_tables=p.raw_tables)
        bigquery.Client(project=project, location=location).query(sql).result()        # multi-statement script
    else:
        import duckdb
        sql = render("local", marts=p.marts, reporting=p.reporting, raw_tables=p.raw_tables)
        con = duckdb.connect(duckdb_path)
        try:
            con.execute(f"create schema if not exists {p.reporting}")
            for st in duckdb_statements(sql, "local"):
                con.execute(st)
        finally:
            con.close()


def run_dbt(p: Profile, backend: str, *, project: str | None, location: str, duckdb_path: str | None, as_of: str,
            target_path: Path | None = None) -> subprocess.CompletedProcess:
    cmd = ["dbt", "build", "--select", "path:models/platform", "--target", "bq_oauth" if backend == "bigquery" else "local",
           "--project-dir", str(ROOT / "dbt/olist"), "--profiles-dir", str(ROOT / "dbt/olist"), "--vars", json.dumps(dbt_vars(p, as_of))]
    if target_path:
        cmd += ["--target-path", str(target_path / "target"), "--log-path", str(target_path / "logs")]
    env = {**os.environ, "DUCKDB_PATH": duckdb_path or "", "GCP_PROJECT_ID": project or "", "BQ_LOCATION": location}
    return subprocess.run(cmd, env=env, capture_output=True, text=True)


def dashboard_command(p: Profile, project: str | None, location: str) -> str:
    return (f'DASH_DATASET={p.reporting} DASH_SIMULATED={1 if p.simulated else 0} DASH_SOURCE_LABEL="{p.label}" DASH_CURRENCY="{p.currency_symbol}" '
            f"GCP_PROJECT_ID={project} BQ_LOCATION={location} python -m streamlit run streamlit_app/platform_dashboard.py")


@dataclass
class ProfileRun:
    ingest: dict[str, RunResult] = field(default_factory=dict)
    dbt_ok: bool | None = None
    dbt_output: str = ""
    recon: ReconciliationSummary | None = None
    quality: list[CheckResult] = field(default_factory=list)
    stale: int = 0


def run_profile(name: str, backend: str, *, engine: Engine, project: str | None = None, location: str = "US",
                duckdb_path: str | None = None, skip_dbt: bool = False, alerts: AlertManager | None = None, now=None,
                target_path: Path | None = None, entities: tuple[str, ...] | None = None) -> ProfileRun:
    p = PROFILES[name]
    os.environ["PLATFORM_PROFILE"] = name
    sink: Sink = BigQuerySink(project, location) if backend == "bigquery" else DuckDBSink(duckdb_path)
    reader: WarehouseReader = BigQueryReader(project, location) if backend == "bigquery" else DuckDBReader(duckdb_path)
    alerts = alerts or AlertManager(engine, LarkNotifier())
    out = ProfileRun()
    db.init_db(engine)
    create_stub_tables(sink, p)
    for entity in entities or p.entities:
        out.ingest[entity] = run_ingestion(p.src, entity, engine=engine, sink=sink, alerts=alerts, now=now)
    as_of = (now or utcnow()).strftime("%Y-%m-%d %H:%M:%S")
    if not skip_dbt:
        res = run_dbt(p, backend, project=project, location=location, duckdb_path=duckdb_path, as_of=as_of, target_path=target_path)
        out.dbt_ok, out.dbt_output = res.returncode == 0, res.stdout[-4000:] + res.stderr[-1500:]
        if not out.dbt_ok:
            return out
        create_reporting_views(p, backend, project=project, location=location, duckdb_path=duckdb_path)
    out.recon = run_reconciliation(reader, engine, load_profile_yaml("reconciliation_rules.yaml"), as_of=now, alerts=alerts)
    out.stale = evaluate_and_alert(check_freshness(engine, load_profile_yaml("freshness.yaml")), alerts)
    out.quality = run_quality_checks(reader, engine, load_profile_yaml("quality.yaml"), alerts=alerts)
    return out
