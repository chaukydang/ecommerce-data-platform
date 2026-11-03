# Architecture

The repository contains two pipelines that share one warehouse project:

1. **Olist batch pipeline** (original): Kaggle → GCS (Parquet) → Airflow → BigQuery `olist_*` → dbt → Streamlit.
2. **Omnichannel platform** (this extension): API sources → Python ingestion framework → BigQuery `raw_*` → dbt `platform_*` →
   reconciliation → monitoring/Lark alerts. **All of its sources are simulated** (`src/ecomplatform/simulators`).

```
 SIMULATED SOURCES (FastAPI)          INGESTION (src/ecomplatform/ingestion)           WAREHOUSE
 marketplace  /v1/marketplace/orders  ┌──────────────────────────────────────┐
 OMS          /v1/oms/orders          │ ApiClient: bearer auth, timeout,     │   raw_marketplace.orders
 POS          /v1/pos/transactions ──►│   429/5xx retry, backoff + jitter    │──►raw_oms.orders
 finance      /v1/finance/{payments,  │ cursor / page pagination             │   raw_pos.transactions
               refunds,settlements}   │ checkpoint (watermark - overlap)     │   raw_finance.{payments,refunds,settlements}
                                      │ schema validation + quarantine       │        │  append-only, one row per
                                      │ idempotent sink (id, record_hash)    │        │  distinct (record_id, hash)
                                      └──────────────┬───────────────────────┘        ▼
                                                     │ audit rows / alerts     dbt  platform_staging (latest version)
                                                     ▼                              platform_intermediate (unified order)
                                  PostgreSQL (platform-db)                          platform_marts (fct_/dim_/mart_)
                                  ingestion_checkpoints, failed_batches                 │
                                  pipeline_run_audit, alert_state                       ▼
                                  data_quality_results               reconciliation engine (Python, rules in YAML)
                                  reconciliation_runs / _exceptions /     ──► reconciliation_exceptions (lifecycle)
                                  _exception_history                  monitoring: freshness, data quality, reliability
                                                                                 ──► Lark webhook (grouped, cooldown, recovery)
```

## Orchestration (Airflow)

| DAG | Schedule | Purpose |
|---|---|---|
| `ingest_marketplace_orders`, `ingest_oms_orders`, `ingest_pos_transactions`, `ingest_financial_records` | */30 min | one task per (source, entity) |
| `transform_ecommerce_warehouse` | hourly | `dbt build --select path:models/platform`, then triggers reconciliation |
| `reconcile_financial_data` | triggered | rule engine → `reconciliation_exceptions` → Lark |
| `monitor_data_quality` | */15 min | freshness + data quality. **Not chained to ingestion on purpose**: a stalled ingestion must still be noticed. |
| `dag_load_static`, `dag_gcs_to_bq` | original | Olist batch pipeline (unchanged) |

Every Airflow task also has an `on_failure_callback` that raises a `PIPELINE_FAILURE` alert, so failures outside the Python
runner (dbt, Airflow itself) are covered.

## Design decisions

| Decision | Why |
|---|---|
| Raw layer keeps **every distinct version** of a record; staging selects the latest | Replays, overlap windows and API retries cannot duplicate data, yet history for late updates/audit is preserved. |
| Checkpoint = max committed `source_updated_at`; next window starts at `watermark − overlap` | Late-arriving updates are re-read; the sink de-duplicates so the overlap is free. |
| Checkpoint advances **only after** all pages are written and validation passed | A crash mid-run leaves committed rows (idempotent) and the same window is retried. |
| Operational metadata in PostgreSQL, not BigQuery | Many small transactional writes/updates (checkpoints, alert state, exception lifecycle) are a poor fit for BigQuery DML. |
| Reconciliation in a Python rule engine **and** a dbt SQL mart | Python owns the exception *lifecycle* (OPEN/INVESTIGATING/…, history, alert dedup); the SQL mart gives BI a per-order view. An integration test proves both flag the same orders. |
| TIMING vs CONFIRMED classification | A missing payment 20 minutes after checkout is sync lag, not a financial break. Only CONFIRMED drives alerts and the unresolved-amount metric. |
| `fct_platform_orders` (not `fct_orders`) | `fct_orders` already exists in the Olist mart; dbt model names are global. |
| Money stored as FLOAT64/DOUBLE, compared with a configurable tolerance | Simple and adequate for reconciliation tolerances; switch to NUMERIC if you need exact ledger arithmetic. |

## Known limits

* Simulated sources only. Real Shopify/TikTok Shop/OMS integration needs real credentials and `normalize()` adjustments in
  the matching connector; the settlement formula must be configured with each marketplace's real accounting rules.
* BigQuery is verified indirectly (fake client, BigQuery-dialect SQL parsing, dbt compile for the BigQuery target) but **never executed against a
  real project** in CI. Everything else runs in CI on DuckDB, SQLite and PostgreSQL.
* The BigQuery MERGE de-duplication compares against the whole target table per batch (no partition pruning is possible for that check);
  fine at this scale, revisit (e.g. restrict to recent partitions) if raw tables grow to billions of rows.
* Schema drift is detected for pass-through connectors (finance) and via data-quality `schema_drift` on the raw tables; for
  connectors that map fields explicitly (marketplace, OMS) a *missing* source field surfaces as rejected records.
