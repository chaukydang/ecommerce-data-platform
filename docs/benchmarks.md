# Query benchmarks (to be measured on BigQuery)

No numbers are claimed here: the BigQuery benchmark has not been run (no GCP project in CI). Run it after the first load:

```bash
python scripts/benchmark_bigquery.py --project <GCP_PROJECT_ID> --location us-east1
```
It performs **dry runs** (no cost) and prints `total_bytes_processed` for each query, so you can compare, for example,
a date-filtered query on `platform_marts.fct_platform_orders` (clustered, deliberately not partitioned because of the sandbox's 60-day partition expiry)
against a full scan, and the clustered vs. unfiltered `mart_daily_sales` channel lookups. Record results in the table below with the date and row counts.

| Date | Query | Rows in table | Bytes processed | Notes |
|---|---|---|---|---|
| | | | | |
