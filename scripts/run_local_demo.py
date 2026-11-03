#!/usr/bin/env python
"""Cloud-free end-to-end demo (SIMULATED data):

    simulator API -> ingestion (retry / checkpoint / idempotent) -> DuckDB raw
      -> dbt (staging / intermediate / marts) -> reconciliation -> freshness + data quality -> alerts

Usage:  python scripts/run_local_demo.py [--workdir .demo] [--orders 400] [--skip-dbt]
        python scripts/run_local_demo.py --backend bigquery --project MY_PROJECT [--location US] [--reset]
The bigquery backend works in the free BigQuery sandbox (no DML / streaming is used); auth = gcloud application-default login.
Set LARK_WEBHOOK_URL (and optionally LARK_WEBHOOK_SECRET) to deliver alerts to Lark; otherwise they are printed.
"""
from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import requests
import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ecomplatform import db  # noqa: E402
from ecomplatform.config import load_sources, load_yaml  # noqa: E402
from ecomplatform.ingestion.core.batch_writer import DuckDBSink  # noqa: E402
from ecomplatform.ingestion.runner import run_ingestion  # noqa: E402
from ecomplatform.monitoring.alerting import AlertManager, LarkNotifier  # noqa: E402
from ecomplatform.monitoring.freshness import check_freshness, evaluate_and_alert  # noqa: E402
from ecomplatform.monitoring.quality import run_quality_checks  # noqa: E402
from ecomplatform.monitoring.reliability import reliability_summary  # noqa: E402
from ecomplatform.reconciliation.engine import run_reconciliation  # noqa: E402
from ecomplatform.reconciliation.readers import DuckDBReader  # noqa: E402
from ecomplatform.reconciliation.store import ExceptionStore  # noqa: E402
from ecomplatform.simulators.generator import generate  # noqa: E402
from ecomplatform.simulators.server import create_app  # noqa: E402
from ecomplatform.util import utcnow  # noqa: E402

TOKEN = "demo-sim-token"
ENTITIES = [("marketplace_sim", "orders"), ("oms_sim", "orders"), ("pos_sim", "transactions"),
            ("finance_sim", "payments"), ("finance_sim", "refunds"), ("finance_sim", "settlements")]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=".demo")
    ap.add_argument("--orders", type=int, default=400)
    ap.add_argument("--skip-dbt", action="store_true")
    ap.add_argument("--backend", choices=["duckdb", "bigquery"], default="duckdb")
    ap.add_argument("--project", default=os.getenv("GCP_PROJECT_ID"))
    ap.add_argument("--location", default=os.getenv("BQ_LOCATION", "US"))
    ap.add_argument("--reset", action="store_true", help="bigquery: drop raw_* and platform_* datasets first")
    a = ap.parse_args()
    bq = a.backend == "bigquery"
    if bq and not a.project:
        ap.error("--project (or GCP_PROJECT_ID) is required for the bigquery backend")
    work = Path(a.workdir).resolve()
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)

    now = utcnow()
    ds = generate(n_orders=a.orders, seed=7, as_of=now - __import__("datetime").timedelta(minutes=1))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(ds, token=TOKEN), host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        try:
            requests.get(f"http://127.0.0.1:{port}/health", timeout=0.5); break
        except requests.RequestException:
            time.sleep(0.05)
    for p in ("MARKETPLACE", "OMS", "POS", "FINANCE"):
        os.environ[f"{p}_API_URL"] = f"http://127.0.0.1:{port}"
        os.environ[f"{p}_API_TOKEN"] = TOKEN

    engine = db.init_db(db.get_engine(f"sqlite:///{work / 'platform.db'}"))
    duck = str(work / "warehouse.duckdb")
    if bq:
        from ecomplatform.ingestion.core.batch_writer import BigQuerySink
        from ecomplatform.reconciliation.readers import BigQueryReader
        if a.reset:
            from google.cloud import bigquery
            client = bigquery.Client(project=a.project, location=a.location)
            for d in ("raw_marketplace", "raw_oms", "raw_pos", "raw_finance", "platform_staging", "platform_intermediate", "platform_marts"):
                client.delete_dataset(f"{a.project}.{d}", delete_contents=True, not_found_ok=True)
            print("   datasets reset")
        sink, reader = BigQuerySink(a.project, a.location), BigQueryReader(a.project, a.location)
    else:
        sink, reader = DuckDBSink(duck), DuckDBReader(duck)
    alerts = AlertManager(engine, LarkNotifier(), cooldown_minutes=60)

    print("== 1. ingestion (simulated sources)")
    cfgs = load_sources()
    for src, ent in ENTITIES:
        r = run_ingestion(src, ent, engine=engine, sink=sink, sources_cfg=cfgs, alerts=alerts)
        print(f"   {src}.{ent:<13} {r.status:<8} extracted={r.extracted:<5} loaded={r.loaded:<5} rejected={r.rejected}")

    as_of = utcnow()
    if not a.skip_dbt:
        print(f"== 2. dbt build ({'BigQuery' if bq else 'DuckDB'} target)")
        env = {**os.environ, "DUCKDB_PATH": duck}
        if bq:
            env.update(GCP_PROJECT_ID=a.project, BQ_LOCATION=a.location)
        cmd = ["dbt", "build", "--select", "path:models/platform", "--target", "bq_oauth" if bq else "local", "--project-dir", str(ROOT / "dbt/olist"),
               "--profiles-dir", str(ROOT / "dbt/olist"), "--vars", f'{{as_of: "{as_of:%Y-%m-%d %H:%M:%S}"}}']
        res = subprocess.run(cmd, env=env, capture_output=True, text=True)
        print("\n".join("   " + l for l in res.stdout.strip().splitlines()[-6:]))
        if res.returncode != 0:
            print(res.stdout[-3000:], res.stderr[-1000:]); return res.returncode

    print("== 3. reconciliation")
    summ = run_reconciliation(reader, engine, load_yaml("reconciliation_rules.yaml"), as_of=as_of, alerts=alerts)
    print(f"   matched {summ.matched_pct}% of {summ.checked} checks | confirmed={summ.confirmed} timing={summ.timing} "
          f"| unresolved difference={ExceptionStore(engine).unresolved_confirmed_amount():.2f}")
    print(ExceptionStore(engine).summary().query("exception_class == 'CONFIRMED'").to_string(index=False))
    print(f"   injected (ground truth): {len(ds.manifest)} discrepancies")

    print("== 4. monitoring")
    evaluate_and_alert(check_freshness(engine, load_yaml("freshness.yaml")), alerts)
    q = run_quality_checks(reader, engine, load_yaml("quality.yaml"), alerts=alerts)
    print(f"   data quality: {sum(r.passed for r in q)}/{len(q)} checks passed")
    print(reliability_summary(engine)[["pipeline_name", "runs", "success_rate_pct", "records_loaded"]].to_string(index=False))
    where = "BigQuery datasets raw_* and platform_*" if bq else "warehouse.duckdb (raw + marts)"
    print(f"\nArtifacts: {work}/platform.db (audit/exceptions) and {where}")
    server.should_exit = True
    return 0


if __name__ == "__main__":
    sys.exit(main())
