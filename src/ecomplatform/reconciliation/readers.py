from __future__ import annotations

from typing import Protocol

import pandas as pd


def latest_version_sql(fq_table: str) -> str:
    """Latest version of every source record (raw layer keeps history). Portable across DuckDB / BigQuery."""
    return (
        f"SELECT * EXCEPT (_rn) FROM ("
        f"SELECT *, ROW_NUMBER() OVER (PARTITION BY source_record_id ORDER BY source_updated_at DESC, ingested_at DESC) AS _rn "
        f"FROM {fq_table}) WHERE _rn = 1"
    )


def naive_utc(df: pd.DataFrame) -> pd.DataFrame:
    """BigQuery TIMESTAMP columns arrive tz-aware (UTC); the engine works with naive UTC like the rest of the platform."""
    for col in df.columns:
        if isinstance(df[col].dtype, pd.DatetimeTZDtype):
            df[col] = df[col].dt.tz_convert("UTC").dt.tz_localize(None)
    return df


class WarehouseReader(Protocol):
    def read_latest(self, fq_table: str) -> pd.DataFrame: ...


class DuckDBReader:
    def __init__(self, path: str):
        self.path = path

    def read_latest(self, fq_table: str) -> pd.DataFrame:
        import duckdb
        con = duckdb.connect(self.path, read_only=True)
        try:
            return con.execute(latest_version_sql(fq_table)).df()
        finally:
            con.close()


class BigQueryReader:
    """Not exercised in CI (needs GCP)."""

    def __init__(self, project: str, location: str = "US", client=None):
        from google.cloud import bigquery
        self.project = project
        self.client = client or bigquery.Client(project=project, location=location)

    def read_latest(self, fq_table: str) -> pd.DataFrame:
        sql = latest_version_sql(f"`{self.project}.{fq_table}`")
        return naive_utc(self.client.query(sql).to_dataframe())
