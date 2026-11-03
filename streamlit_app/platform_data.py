"""Data access for the platform dashboard. Reads the `platform_reporting` views (sql/bigquery_dashboard_views.sql).

Backends
  bigquery (default when GCP_PROJECT_ID is set): uses Application Default Credentials (gcloud auth application-default login)
  duckdb   (DASH_DUCKDB_PATH): offline demo/test against the file written by `scripts/run_local_demo.py`
Other env: DASH_DATASET (reporting dataset, e.g. platform_reporting_shopify), DASH_SIMULATED=0 (real-data labels)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd

VIEWS_SQL = Path(__file__).resolve().parents[1] / "sql" / "bigquery_dashboard_views.sql"


def backend_name() -> str:
    if os.getenv("DASH_DUCKDB_PATH"):
        return "duckdb"
    return "bigquery"


def normalise(df: pd.DataFrame) -> pd.DataFrame:
    """Plain pandas types: tz-naive timestamps, python-friendly dates/ints."""
    for c in df.columns:
        s = df[c]
        if isinstance(s.dtype, pd.DatetimeTZDtype):
            df[c] = s.dt.tz_convert("UTC").dt.tz_localize(None)
        elif str(s.dtype) == "dbdate" or c.endswith("_date"):
            df[c] = pd.to_datetime(s)
        elif str(s.dtype).startswith(("Int", "UInt")):
            df[c] = s.astype("float64")
    return df


class BigQueryBackend:
    def __init__(self, project: str, location: str = "US", dataset: str = "platform_reporting"):
        from google.cloud import bigquery
        self.project, self.dataset = project, dataset
        self.client = bigquery.Client(project=project, location=location)

    def view(self, name: str) -> pd.DataFrame:
        return normalise(self.client.query(f"SELECT * FROM `{self.project}.{self.dataset}.{name}`").to_dataframe())


class DuckDBBackend:
    """Creates the reporting views in the DuckDB file (BigQuery SQL transpiled with sqlglot) if they are missing."""

    def __init__(self, path: str, dataset: str = "platform_reporting", marts: str = "platform_marts",
                 raw_tables: list[str] | None = None):
        self.path, self.dataset, self.marts, self.raw_tables = path, dataset, marts, raw_tables
        self._ensure_views()

    def _con(self, read_only: bool = True):
        import duckdb
        return duckdb.connect(self.path, read_only=read_only)

    def _ensure_views(self) -> None:
        con = self._con(read_only=False)
        try:
            have = con.execute("select count(*) from information_schema.tables where table_schema = ?", [self.dataset]).fetchone()[0]
            if have >= 7:
                return
            try:
                from ecomplatform.reporting_views import duckdb_statements, render
            except ImportError:                                           # running without `pip install -e .`
                sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
                from ecomplatform.reporting_views import duckdb_statements, render
            sql = render("local", marts=self.marts, reporting=self.dataset, raw_tables=self.raw_tables)
            con.execute(f"create schema if not exists {self.dataset}")
            for st in duckdb_statements(sql, "local"):
                con.execute(st)
        finally:
            con.close()

    def view(self, name: str) -> pd.DataFrame:
        con = self._con()
        try:
            return normalise(con.execute(f"select * from {self.dataset}.{name}").df())
        finally:
            con.close()


def get_backend():
    if backend_name() == "duckdb":
        raw = [t for t in os.getenv("DASH_RAW_TABLES", "").split(",") if t] or None
        return DuckDBBackend(os.environ["DASH_DUCKDB_PATH"], os.getenv("DASH_DATASET", "platform_reporting"),
                             os.getenv("DASH_MARTS", "platform_marts"), raw)
    project = os.getenv("GCP_PROJECT_ID")
    if not project:
        raise RuntimeError("Set GCP_PROJECT_ID (BigQuery) or DASH_DUCKDB_PATH (offline demo).")
    return BigQueryBackend(project, os.getenv("BQ_LOCATION", "US"), os.getenv("DASH_DATASET", "platform_reporting"))
