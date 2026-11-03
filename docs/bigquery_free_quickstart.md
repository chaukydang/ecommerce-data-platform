# Run on your own BigQuery - free (sandbox) quickstart

Works **without a credit card** in the BigQuery sandbox. The platform avoids DML and streaming (not available in the sandbox):
loads use append load jobs and de-duplication uses a `SELECT` of existing `(record id, hash)` pairs (`BQ_DEDUPE=select`, the default).
dbt only issues DDL (`CREATE VIEW/TABLE`). Sandbox caveat: **tables/views/partitions expire after 60 days**.

## 1. One-time setup

1. https://console.cloud.google.com → create a project (note the **Project ID**) → open **BigQuery** (this enables the sandbox).
2. Install the [gcloud CLI](https://cloud.google.com/sdk/docs/install), then:
   ```bash
   gcloud auth login
   gcloud auth application-default login          # credentials used by Python + dbt
   gcloud config set project YOUR_PROJECT_ID
   gcloud auth application-default set-quota-project YOUR_PROJECT_ID
   ```
3. ```bash
   git clone <your-repo-url> && cd <repo>
   python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
   pip install -e ".[sim,local,bigquery,dev]"
   ```

## 2. Check locally first (no cloud)

```bash
python -m pytest tests -q          # ~50 tests, a few minutes at most
python scripts/run_local_demo.py   # full flow on DuckDB
```

## 3. Run the full flow on BigQuery

```bash
python scripts/run_local_demo.py --backend bigquery --project YOUR_PROJECT_ID --location US --workdir .demo_bq --reset
# or: make demo-bq PROJECT=YOUR_PROJECT_ID
```
It starts the simulator, ingests 6 entities into `raw_*` datasets, runs `dbt build` (target `bq_oauth`) into `platform_*`, reconciles
from BigQuery, and runs freshness + data-quality checks. Expect: 30 confirmed exceptions = 30 injected discrepancies, 29/29 DQ checks pass.
`--reset` drops the `raw_*`/`platform_*` datasets first (re-runs append new record versions otherwise).

Look at the result in the console:
```sql
select * from `YOUR_PROJECT_ID.platform_marts.mart_financial_reconciliation`
where payment_check = 'MISSING' or settlement_check = 'FORMULA_MISMATCH';

select order_date, channel, orders, gross_sales, net_sales from `YOUR_PROJECT_ID.platform_marts.mart_daily_sales` order by 1 desc;
```
Audit log / reconciliation exceptions live in the local SQLite file `.demo_bq/platform.db` (PostgreSQL in the Docker Compose stack).

## 4. Troubleshooting

| Symptom | Fix |
|---|---|
| `Could not automatically determine credentials` / dbt oauth error | run `gcloud auth application-default login` again |
| `403 ... bigquery.jobs.create` | wrong project in `--project`, or the account is not Owner/Editor of it |
| `Not found: Dataset ... was not found in location X` | datasets were created in another location; use the same `--location` everywhere or run with `--reset` |
| `Quota exceeded` / `billingNotEnabled` for DML | you set `BQ_DEDUPE=merge`; use the default `select` in the sandbox |
| Tables vanished after ~2 months | sandbox 60-day expiry - re-run the demo or enable billing and clear the expiration |

## 5. Clean up

```bash
for d in raw_marketplace raw_oms raw_pos raw_finance platform_staging platform_intermediate platform_marts; do bq rm -r -f -d YOUR_PROJECT_ID:$d; done
```
