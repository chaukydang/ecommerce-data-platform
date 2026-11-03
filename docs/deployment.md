# Deployment

## 0. Local, no cloud (fastest check)

```bash
pip install -e ".[sim,local,dev]"
make test      # unit + simulator/DuckDB/dbt integration + BigQuery fake/compile checks (set TEST_DB_URL to also run on PostgreSQL)
make demo      # simulator -> ingest -> dbt(DuckDB) -> reconcile -> freshness/DQ -> alerts
```
Set `LARK_WEBHOOK_URL` (+ `LARK_WEBHOOK_SECRET` if the bot uses signature verification) to deliver alerts to Lark; otherwise they are logged.

## 1. GCP (BigQuery)

```bash
cd terraform && terraform init && terraform apply        # adds raw_* and platform_* datasets next to the olist_* ones
```
Least privilege: give the Airflow service account `BigQuery Data Editor` on the `raw_*` and `platform_*` datasets, `BigQuery Job User`
on the project and `Storage Object Viewer` only where the Olist DAGs need it. Do not reuse an owner key.

## 2. Airflow + platform DB + simulator

```bash
cp .env.example .env     # fill GCP_*, optionally LARK_*
cd airflow && docker compose up -d --build
```
Services: Airflow (8080), `platform-db` PostgreSQL (5433), `simulator` (8000). The compose image is `apache/airflow:2.8.1-python3.11`
(the default 3.8 image cannot run this code). Un-pause the `ingest_*`, `transform_ecommerce_warehouse`, `monitor_data_quality` DAGs.

## 3. dbt

```bash
dbt build --select path:models/platform --target prod  --project-dir dbt/olist --profiles-dir dbt/olist   # BigQuery
DUCKDB_PATH=warehouse.duckdb dbt build --select path:models/platform --target local ...                   # DuckDB
```
`--vars '{as_of: "YYYY-MM-DD HH:MM:SS"}'` pins the timing/confirmed cut-off (Airflow passes the data interval end).

## 4. Rollback

* Code: redeploy the previous git tag; DAGs and `src/` are volume-mounted.
* Data: raw is append-only — a bad batch is identifiable by `batch_id`; delete those rows and reset the checkpoint (`docs/operational_runbook.md`).
  dbt models are rebuilt from raw: `dbt build --full-refresh --select path:models/platform`.
* Exceptions: `reconciliation_exceptions` self-heals on the next run; manual states (`ACCEPTED_EXCEPTION`) are preserved.

## Replacing a simulator with a real API

1. Add/adjust the source in `configs/sources.yaml` (`base_url_env`, `token_env`, `path`, pagination, schema).
2. Implement `normalize()` in the matching connector (`ingestion/connectors/*`) — vocabulary and money-unit mapping live there.
3. Keep the canonical schema; staging, reconciliation and monitoring need no change.
