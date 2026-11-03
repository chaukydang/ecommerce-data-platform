#!/usr/bin/env python
"""Run the platform on REAL Shopify data (a Shopify development store).

    export SHOPIFY_STORE_URL=https://your-store.myshopify.com
    export SHOPIFY_CLIENT_ID=...  SHOPIFY_CLIENT_SECRET=...         # Dev Dashboard app (or SHOPIFY_ACCESS_TOKEN=...)
    python scripts/run_shopify.py --backend bigquery --project YOUR_PROJECT --location US
    python scripts/run_shopify.py --backend duckdb                    # offline warehouse file, same logic

Then:  DASH_DATASET=platform_reporting_shopify DASH_SIMULATED=0 GCP_PROJECT_ID=YOUR_PROJECT python -m streamlit run streamlit_app/platform_dashboard.py
See docs/shopify_quickstart.md.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ecomplatform import db  # noqa: E402
from ecomplatform.ingestion.core.retry_policy import IngestionError  # noqa: E402
from ecomplatform.reconciliation.store import ExceptionStore  # noqa: E402
from ecomplatform.shopify_pipeline import REPORTING, run_shopify  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", choices=["duckdb", "bigquery"], default="duckdb")
    ap.add_argument("--project", default=os.getenv("GCP_PROJECT_ID"))
    ap.add_argument("--location", default=os.getenv("BQ_LOCATION", "US"))
    ap.add_argument("--workdir", default=".shopify")
    ap.add_argument("--skip-dbt", action="store_true")
    a = ap.parse_args()
    if a.backend == "bigquery" and not a.project:
        ap.error("--project (or GCP_PROJECT_ID) is required for the bigquery backend")
    work = Path(a.workdir).resolve()
    work.mkdir(parents=True, exist_ok=True)
    engine = db.init_db(db.get_engine(f"sqlite:///{work / 'platform.db'}"))
    duck = str(work / "warehouse.duckdb")
    try:
        r = run_shopify(a.backend, engine=engine, project=a.project, location=a.location, duckdb_path=duck, skip_dbt=a.skip_dbt)
    except IngestionError as exc:
        print(f"\nIngestion failed ({exc.category}): {exc}\nCheck SHOPIFY_STORE_URL and the credentials (docs/shopify_quickstart.md).")
        return 2
    print("== ingestion (Shopify Admin REST API)")
    for e, res in r.ingest.items():
        print(f"   {e:<9} {res.status:<8} extracted={res.extracted:<5} loaded={res.loaded:<5} rejected={res.rejected} retries={res.retries}")
    if r.dbt_ok is False:
        print("== dbt build FAILED\n" + r.dbt_output)
        return 1
    if r.recon:
        print(f"== reconciliation: matched {r.recon.matched_pct}% of {r.recon.checked} checks | confirmed={r.recon.confirmed} "
              f"timing={r.recon.timing} | unresolved={ExceptionStore(engine).unresolved_confirmed_amount():.2f}")
    print(f"== monitoring: freshness violations={r.stale}, data quality {sum(q.passed for q in r.quality)}/{len(r.quality)} checks passed")
    if a.backend == "bigquery":
        print(f"\nDashboard:\n  DASH_DATASET={REPORTING} DASH_SIMULATED=0 GCP_PROJECT_ID={a.project} BQ_LOCATION={a.location} "
              f"python -m streamlit run streamlit_app/platform_dashboard.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
