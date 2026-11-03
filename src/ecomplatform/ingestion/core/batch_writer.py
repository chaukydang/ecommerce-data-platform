"""Batch enrichment + idempotent sinks (DuckDB for local/dev/tests, BigQuery for GCP).

Raw-layer contract (both sinks): append-only history of *distinct versions*.
A row is inserted only if no row with the same (source_record_id, record_hash) exists, so
re-running a batch, overlapping windows and API retries can never create duplicates, while a
genuinely updated record (different hash) is kept as a new version for the staging layer to pick.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Protocol

from ecomplatform.util import parse_iso, record_hash, utcnow

META_COLUMNS = [
    ("source_system", "string"),
    ("source_record_id", "string"),
    ("source_updated_at", "timestamp"),
    ("ingested_at", "timestamp"),
    ("batch_id", "string"),
    ("record_hash", "string"),
]


def enrich(records: list[dict], schema: dict[str, Any], source_system: str, batch_id: str,
           ingested_at: datetime | None = None) -> list[dict]:
    """Attach ingestion metadata; keep only schema fields; convert timestamps to naive UTC datetimes."""
    ingested_at = ingested_at or utcnow()
    key_fields: list[str] = schema["key"]
    updated_field: str = schema["updated_field"]
    ts_fields = [n for n, s in schema["fields"].items() if s["type"] == "timestamp"]
    out = []
    for rec in records:
        row = {n: rec.get(n) for n in schema["fields"]}
        h = record_hash(row)
        for f in ts_fields:
            row[f] = parse_iso(row[f])
        row.update(
            source_system=source_system,
            source_record_id=":".join(str(rec[k]) for k in key_fields),
            source_updated_at=parse_iso(rec[updated_field]),
            ingested_at=ingested_at,
            batch_id=batch_id,
            record_hash=h,
        )
        out.append(row)
    return out


def dedupe_in_batch(rows: list[dict]) -> list[dict]:
    seen, out = set(), []
    for r in rows:
        k = (r["source_record_id"], r["record_hash"])
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def columns_for(schema: dict[str, Any]) -> list[tuple[str, str]]:
    return [(n, s["type"]) for n, s in schema["fields"].items()] + META_COLUMNS


class Sink(Protocol):
    def write(self, dataset: str, table: str, schema: dict, rows: list[dict]) -> int: ...
    def write_rejected(self, dataset: str, table: str, batch_id: str, rejected: list[tuple[dict, str]]) -> None: ...


# --------------------------------------------------------------------------------------
class DuckDBSink:
    _TYPES = {"string": "VARCHAR", "integer": "BIGINT", "number": "DOUBLE", "timestamp": "TIMESTAMP"}

    def __init__(self, path: str = "warehouse.duckdb"):
        import duckdb
        self.path = path
        self._duckdb = duckdb

    def _con(self):
        return self._duckdb.connect(self.path)

    def write(self, dataset: str, table: str, schema: dict, rows: list[dict]) -> int:
        import pandas as pd
        cols = columns_for(schema)
        ddl = ", ".join(f'"{n}" {self._TYPES[t]}' for n, t in cols)
        fq = f'"{dataset}"."{table}"'
        rows = dedupe_in_batch(rows)
        con = self._con()
        try:
            con.execute(f'CREATE SCHEMA IF NOT EXISTS "{dataset}"')
            con.execute(f"CREATE TABLE IF NOT EXISTS {fq} ({ddl})")
            if not rows:
                return 0
            df = pd.DataFrame(rows, columns=[n for n, _ in cols])
            con.register("batch_df", df)
            before = con.execute(f"SELECT count(*) FROM {fq}").fetchone()[0]
            col_list = ", ".join(f'"{n}"' for n, _ in cols)
            con.execute(
                f"INSERT INTO {fq} ({col_list}) SELECT {', '.join('b.' + chr(34) + n + chr(34) for n, _ in cols)} "
                f"FROM batch_df b WHERE NOT EXISTS (SELECT 1 FROM {fq} t "
                f"WHERE t.source_record_id = b.source_record_id AND t.record_hash = b.record_hash)"
            )
            return con.execute(f"SELECT count(*) FROM {fq}").fetchone()[0] - before
        finally:
            con.close()

    def write_rejected(self, dataset: str, table: str, batch_id: str, rejected: list[tuple[dict, str]]) -> None:
        if not rejected:
            return
        con = self._con()
        try:
            con.execute(f'CREATE SCHEMA IF NOT EXISTS "{dataset}"')
            fq = f'"{dataset}"."{table}__rejected"'
            con.execute(f"CREATE TABLE IF NOT EXISTS {fq} (batch_id VARCHAR, reason VARCHAR, payload VARCHAR, rejected_at TIMESTAMP)")
            now = utcnow()
            for rec, reason in rejected:
                con.execute(f"INSERT INTO {fq} VALUES (?, ?, ?, ?)", [batch_id, reason, json.dumps(rec, default=str), now])
        finally:
            con.close()

    def query_df(self, sql: str):
        con = self._con()
        try:
            return con.execute(sql).df()
        finally:
            con.close()


# --------------------------------------------------------------------------------------
class BigQuerySink:
    """Idempotent loads into BigQuery. Two de-duplication strategies:

    * ``dedupe="select"`` (default): SELECT the (source_record_id, record_hash) pairs already present for this batch's ids and
      load only the new rows with an append load job. Uses **no DML**, so it also works in the free BigQuery *sandbox*
      (which does not support DML or streaming).
    * ``dedupe="merge"``: load into a temp table and MERGE unseen pairs (atomic; needs billing enabled because MERGE is DML).

    NOTE: verified with a fake client + BigQuery-dialect SQL parsing; not executed against a real project in CI.
    """
    _TYPES = {"string": "STRING", "integer": "INT64", "number": "FLOAT64", "timestamp": "TIMESTAMP"}

    def __init__(self, project: str, location: str = "US", client=None, dedupe: str = "select"):
        if dedupe not in ("select", "merge"):
            raise ValueError("dedupe must be 'select' or 'merge'")
        self.dedupe = dedupe
        from google.api_core.exceptions import NotFound
        from google.cloud import bigquery
        self._NotFound = NotFound
        self.bq = bigquery
        self.project = project
        self.location = location
        self.client = client or bigquery.Client(project=project, location=location)

    def _ensure_table(self, dataset: str, table: str, schema: dict):
        try:                                            # datasets are normally created by Terraform; avoid needing
            self.client.get_dataset(f"{self.project}.{dataset}")   # bigquery.datasets.create for the pipeline account
        except self._NotFound:
            ds = self.bq.Dataset(f"{self.project}.{dataset}")
            ds.location = self.location
            self.client.create_dataset(ds, exists_ok=True)
        fields = [self.bq.SchemaField(n, self._TYPES[t]) for n, t in columns_for(schema)]
        tbl = self.bq.Table(f"{self.project}.{dataset}.{table}", schema=fields)
        tbl.time_partitioning = self.bq.TimePartitioning(field="ingested_at")
        tbl.clustering_fields = ["source_record_id"]
        self.client.create_table(tbl, exists_ok=True)
        return fields

    def write(self, dataset: str, table: str, schema: dict, rows: list[dict]) -> int:
        fields = self._ensure_table(dataset, table, schema)
        rows = dedupe_in_batch(rows)
        if not rows:
            return 0
        target = f"`{self.project}.{dataset}.{table}`"
        if self.dedupe == "select":
            return self._append_new_rows(dataset, table, target, fields, rows)
        tmp_id = f"{self.project}.{dataset}.{table}__tmp_{rows[0]['batch_id']}_{uuid.uuid4().hex[:8]}"
        job = self.client.load_table_from_json(
            self._payload(rows), tmp_id,
            job_config=self.bq.LoadJobConfig(schema=fields, write_disposition="WRITE_TRUNCATE"),
        )
        job.result()
        try:
            cols = ", ".join(f"`{n}`" for n, _ in columns_for(schema))
            vals = ", ".join(f"S.`{n}`" for n, _ in columns_for(schema))     # qualified: T and S share column names
            merge = (f"MERGE {target} T USING `{tmp_id}` S "
                     f"ON T.source_record_id = S.source_record_id AND T.record_hash = S.record_hash "
                     f"WHEN NOT MATCHED THEN INSERT ({cols}) VALUES ({vals})")
            job = self.client.query(merge)
            job.result()
            return int(job.num_dml_affected_rows or 0)
        finally:
            self.client.delete_table(tmp_id, not_found_ok=True)

    @staticmethod
    def _payload(rows: list[dict]) -> list[dict]:
        return [{k: (v.strftime("%Y-%m-%d %H:%M:%S") if isinstance(v, datetime) else v) for k, v in r.items()} for r in rows]

    def _append_new_rows(self, dataset: str, table: str, target: str, fields, rows: list[dict]) -> int:
        ids = sorted({r["source_record_id"] for r in rows})
        cfg = self.bq.QueryJobConfig(query_parameters=[self.bq.ArrayQueryParameter("ids", "STRING", ids)])
        sql = f"SELECT source_record_id, record_hash FROM {target} WHERE source_record_id IN UNNEST(@ids)"
        seen = {(r["source_record_id"], r["record_hash"]) for r in self.client.query(sql, job_config=cfg).result()}
        new = [r for r in rows if (r["source_record_id"], r["record_hash"]) not in seen]
        if new:
            self.client.load_table_from_json(
                self._payload(new), f"{self.project}.{dataset}.{table}",
                job_config=self.bq.LoadJobConfig(schema=fields, write_disposition="WRITE_APPEND")).result()
        return len(new)

    def write_rejected(self, dataset: str, table: str, batch_id: str, rejected: list[tuple[dict, str]]) -> None:
        if not rejected:
            return
        tid = f"{self.project}.{dataset}.{table}__rejected"
        schema = [self.bq.SchemaField(n, t) for n, t in
                  [("batch_id", "STRING"), ("reason", "STRING"), ("payload", "STRING"), ("rejected_at", "TIMESTAMP")]]
        self.client.create_table(self.bq.Table(tid, schema=schema), exists_ok=True)
        now = utcnow().strftime("%Y-%m-%d %H:%M:%S")
        # load job (not streaming insert): streaming is unavailable on the free/sandbox tier and rows are not immediately queryable
        self.client.load_table_from_json(
            [{"batch_id": batch_id, "reason": reason, "payload": json.dumps(rec, default=str), "rejected_at": now}
             for rec, reason in rejected],
            tid, job_config=self.bq.LoadJobConfig(schema=schema, write_disposition="WRITE_APPEND")).result()
