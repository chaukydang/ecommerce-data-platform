"""BigQuery code paths verified WITHOUT a GCP project:
 * the sink / reader are driven through a faithful fake of google.cloud.bigquery.Client (call sequence, arguments)
 * every SQL string we send is parsed with the BigQuery dialect (sqlglot)
This does not replace one real run on GCP, but it catches API misuse, SQL syntax errors and type problems."""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

pytest.importorskip("google.cloud.bigquery")
sqlglot = pytest.importorskip("sqlglot")
from google.api_core.exceptions import NotFound  # noqa: E402

from ecomplatform.config import entity_config, load_sources  # noqa: E402
from ecomplatform.ingestion.core.batch_writer import BigQuerySink, enrich  # noqa: E402
from ecomplatform.reconciliation.readers import BigQueryReader, latest_version_sql, naive_utc  # noqa: E402

NOW = datetime(2026, 11, 3, 10, 0, 0)


class FakeJob:
    def __init__(self, affected=None, fail=False):
        self.num_dml_affected_rows, self._fail = affected, fail

    def result(self):
        if self._fail:
            raise RuntimeError("merge failed")
        return iter(())


class FakeClient:
    def __init__(self, dataset_exists=True, merge_fails=False, affected=2, df=None):
        self.dataset_exists, self.merge_fails, self.affected, self.df = dataset_exists, merge_fails, affected, df
        self.calls: list[tuple] = []
        self.existing: list[dict] = []
        self.queries: list[str] = []

    def get_dataset(self, ref):
        self.calls.append(("get_dataset", ref))
        if not self.dataset_exists:
            raise NotFound("nope")

    def create_dataset(self, ds, exists_ok=False):
        self.calls.append(("create_dataset", ds.dataset_id, ds.location))

    def create_table(self, tbl, exists_ok=False):
        self.calls.append(("create_table", tbl.full_table_id if hasattr(tbl, "full_table_id") else str(tbl.reference), tbl))

    def load_table_from_json(self, rows, dest, job_config=None):
        self.calls.append(("load", dest, len(rows), job_config.write_disposition, [f.name for f in job_config.schema], rows))
        return FakeJob()

    def query(self, sql, job_config=None):
        self.queries.append(sql)
        self.calls.append(("query", sql, job_config))
        if sql.lstrip().startswith("MERGE"):
            return FakeJob(self.affected, fail=self.merge_fails)
        if sql.lstrip().upper().startswith("SELECT SOURCE_RECORD_ID"):
            return type("Q", (), {"result": lambda _s: iter(self.existing)})()
        return type("Q", (), {"to_dataframe": lambda _s: self.df.copy()})()

    def delete_table(self, tid, not_found_ok=False):
        self.calls.append(("delete_table", tid))


def _rows(n=2):
    cfg = entity_config(load_sources(), "marketplace_sim", "orders")
    recs = [{"order_id": f"O{i}", "channel": "c", "status": "PAID", "total_amount": 1.5, "currency": "USD",
             "customer_id": "C", "created_at": "2026-11-03T09:00:00Z", "updated_at": "2026-11-03T09:00:00Z"} for i in range(n)]
    return cfg["schema"], enrich(recs, cfg["schema"], "marketplace_sim", "b1", ingested_at=NOW)


def test_sink_call_sequence_merge_sql_and_cleanup():
    schema, rows = _rows()
    c = FakeClient(affected=2)
    sink = BigQuerySink("proj", "us-east1", client=c, dedupe="merge")
    assert sink.write("raw_marketplace", "orders", schema, rows) == 2
    kinds = [x[0] for x in c.calls]
    assert kinds == ["get_dataset", "create_table", "load", "query", "delete_table"]
    tbl = c.calls[1][2]
    assert tbl.time_partitioning.field == "ingested_at" and tbl.clustering_fields == ["source_record_id"]
    load = c.calls[2]
    assert load[3] == "WRITE_TRUNCATE" and {"order_id", "record_hash", "ingested_at", "batch_id"} <= set(load[4])
    assert load[5][0]["created_at"] == "2026-11-03 09:00:00"                      # datetimes serialised for BigQuery
    merge = c.queries[0]
    sqlglot.parse_one(merge, read="bigquery")                                      # valid BigQuery syntax
    assert "WHEN NOT MATCHED THEN INSERT" in merge and "S.`order_id`" in merge     # qualified source columns
    assert "T.record_hash = S.record_hash" in merge
    assert c.calls[-1][1] == c.calls[2][1]                                         # temp table deleted


def test_default_select_mode_uses_no_dml_and_loads_only_new_rows():
    """Works in the free BigQuery sandbox: no MERGE/INSERT/UPDATE/DELETE, no streaming, no temp table."""
    schema, rows = _rows(3)
    c = FakeClient()
    c.existing = [{"source_record_id": rows[0]["source_record_id"], "record_hash": rows[0]["record_hash"]}]   # already loaded
    sink = BigQuerySink("proj", "us-east1", client=c)
    assert sink.dedupe == "select" and sink.write("raw_marketplace", "orders", schema, rows) == 2
    sqls = [q.upper() for q in c.queries]
    assert len(sqls) == 1 and sqls[0].startswith("SELECT") and not any(k in sqls[0] for k in ("MERGE", "INSERT", "UPDATE", "DELETE"))
    sqlglot.parse_one(c.queries[0], read="bigquery")
    assert [x[0] for x in c.calls] == ["get_dataset", "create_table", "query", "load"]      # nothing to clean up
    load = c.calls[-1]
    assert load[3] == "WRITE_APPEND" and load[2] == 2 and load[1] == "proj.raw_marketplace.orders"
    assert {r["order_id"] for r in load[5]} == {"O1", "O2"}                                  # the already-present row is skipped


def test_select_mode_rerun_loads_nothing():
    schema, rows = _rows(2)
    c = FakeClient()
    c.existing = [{"source_record_id": r["source_record_id"], "record_hash": r["record_hash"]} for r in rows]
    assert BigQuerySink("proj", client=c).write("d", "t", schema, rows) == 0
    assert not [x for x in c.calls if x[0] == "load"]


def test_temp_table_deleted_even_if_merge_fails_and_error_propagates():
    schema, rows = _rows()
    c = FakeClient(merge_fails=True)
    with pytest.raises(RuntimeError):
        BigQuerySink("proj", client=c, dedupe="merge").write("raw_marketplace", "orders", schema, rows)
    assert c.calls[-1][0] == "delete_table"


def test_missing_dataset_is_created_in_configured_location():
    schema, rows = _rows()
    c = FakeClient(dataset_exists=False)
    BigQuerySink("proj", "us-east1", client=c, dedupe="merge").write("raw_marketplace", "orders", schema, rows)
    assert ("create_dataset", "raw_marketplace", "us-east1") in c.calls


def test_empty_batch_issues_no_merge_and_duplicates_in_batch_are_dropped():
    schema, rows = _rows()
    c = FakeClient()
    sink = BigQuerySink("proj", client=c, dedupe="merge")
    assert sink.write("d", "t", schema, []) == 0 and not c.queries
    sink.write("d", "t", schema, rows + rows)
    assert c.calls[[x[0] for x in c.calls].index("load")][2] == len(rows)


def test_rejected_rows_use_load_job_not_streaming():
    c = FakeClient()
    BigQuerySink("proj", client=c).write_rejected("raw_marketplace", "orders", "b1", [({"order_id": "x"}, "bad")])
    load = [x for x in c.calls if x[0] == "load"][0]
    assert load[3] == "WRITE_APPEND" and load[1].endswith("orders__rejected")


def test_reader_sql_is_valid_bigquery_and_timestamps_become_naive_utc():
    sql = latest_version_sql("`p`.`raw_finance`.`payments`")
    sqlglot.parse_one(sql, read="bigquery")
    assert "EXCEPT" not in sql.upper() and "EXCLUDE" not in sql.upper()          # dialect-neutral
    df = pd.DataFrame({"order_id": ["a"], "_rn": [1], "created_at": pd.to_datetime(["2026-11-03T09:00:00Z"], utc=True), "amount": [1.0]})
    out = BigQueryReader("proj", client=FakeClient(df=df)).read_latest("raw_finance.payments")
    assert "_rn" not in out.columns and out["created_at"].dt.tz is None and out["created_at"][0] == pd.Timestamp("2026-11-03 09:00:00")
    # and the engine's timing logic accepts it (tz-aware vs naive comparison would raise)
    from ecomplatform.reconciliation.engine import _klass
    assert _klass(out["created_at"][0], NOW, 2) in ("TIMING", "CONFIRMED")
    assert naive_utc(pd.DataFrame({"x": [1]})).equals(pd.DataFrame({"x": [1]}))
