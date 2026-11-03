#!/usr/bin/env python
"""Run the platform on a real / real-shaped source profile: shopify | shopee | olist | omni.

    python scripts/run_profile.py --profile olist  --backend bigquery --project YOUR_PROJECT     # needs OLIST_DATA_DIR (Kaggle CSVs)
    python scripts/run_profile.py --profile shopee --backend bigquery --project YOUR_PROJECT     # needs SHOPEE_* credentials
    python scripts/run_profile.py --profile omni   --backend bigquery --project YOUR_PROJECT     # simulated Shopee/TikTok/Lazada/Web/POS
    python scripts/run_profile.py --profile shopify --backend duckdb                             # offline warehouse file

See docs/olist_quickstart.md, docs/shopee_quickstart.md, docs/shopify_quickstart.md.
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
from ecomplatform.profile_pipeline import PROFILES, dashboard_command, run_profile  # noqa: E402
from ecomplatform.reconciliation.store import ExceptionStore  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", choices=sorted(PROFILES), required=True)
    ap.add_argument("--backend", choices=["duckdb", "bigquery"], default="duckdb")
    ap.add_argument("--project", default=os.getenv("GCP_PROJECT_ID"))
    ap.add_argument("--location", default=os.getenv("BQ_LOCATION", "US"))
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--skip-dbt", action="store_true")
    ap.add_argument("--download-olist", action="store_true", help="olist: fetch the dataset with the Kaggle CLI first (needs Kaggle credentials)")
    a = ap.parse_args()
    if a.backend == "bigquery" and not a.project:
        ap.error("--project (or GCP_PROJECT_ID) is required for the bigquery backend")
    work = Path(a.workdir or f".{a.profile}").resolve()
    if a.profile == "olist" and a.download_olist:
        from ecomplatform.ingestion.connectors.olist_connector import download_olist
        try:
            os.environ["OLIST_DATA_DIR"] = str(download_olist(work / "data"))
        except IngestionError as exc:
            print(f"\n{exc}")
            return 2
        print(f"Olist CSV files ready in {os.environ['OLIST_DATA_DIR']}")
    work.mkdir(parents=True, exist_ok=True)
    engine = db.init_db(db.get_engine(f"sqlite:///{work / 'platform.db'}"))
    try:
        r = run_profile(a.profile, a.backend, engine=engine, project=a.project, location=a.location,
                        duckdb_path=str(work / "warehouse.duckdb"), skip_dbt=a.skip_dbt)
    except IngestionError as exc:
        print(f"\nIngestion failed ({exc.category}): {exc}\nCheck the credentials / data folder (docs/{a.profile}_quickstart.md).")
        return 2
    print(f"== ingestion ({a.profile})")
    for e, res in r.ingest.items():
        print(f"   {e:<12} {res.status:<8} extracted={res.extracted:<7} loaded={res.loaded:<7} rejected={res.rejected} retries={res.retries}")
    if r.dbt_ok is False:
        print("== dbt build FAILED\n" + r.dbt_output)
        return 1
    if r.recon:
        print(f"== reconciliation: matched {r.recon.matched_pct}% of {r.recon.checked} checks | confirmed={r.recon.confirmed} "
              f"timing={r.recon.timing} | unresolved={ExceptionStore(engine).unresolved_confirmed_amount():.2f}")
    print(f"== monitoring: freshness violations={r.stale}, data quality {sum(q.passed for q in r.quality)}/{len(r.quality)} checks passed")
    if a.backend == "bigquery":
        print("\nDashboard:\n  " + dashboard_command(PROFILES[a.profile], a.project, a.location))
    return 0


if __name__ == "__main__":
    sys.exit(main())
