![Python](https://img.shields.io/badge/Python-3.11-blue)
![Terraform](https://img.shields.io/badge/Terraform-1.14-purple)
![Airflow](https://img.shields.io/badge/Airflow-2.8.1-red)
![dbt](https://img.shields.io/badge/dbt-1.8.7-orange)
![BigQuery](https://img.shields.io/badge/BigQuery-GCP-orange)
![Streamlit](https://img.shields.io/badge/Streamlit-Dashboard-ff4b4b)

# E-Commerce Data Pipeline

A production-grade batch data pipeline built on GCP that ingests, transforms, and analyzes the [Olist Brazilian E-Commerce dataset](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce) from Kaggle.

## Problem Statement

Build a scalable batch data pipeline on GCP to ingest, transform, and analyze Olist e-commerce data — enabling visibility into order trends, delivery performance, and seller revenue through a structured data warehouse and dashboard.

## Architecture

![Pipeline Architecture](docs/ecommerce_pipeline_architecture.svg)

```
Kaggle (Source)
      ↓
Python Ingestion Script (partitions CSVs by month → Parquet)
      ↓
Google Cloud Storage (raw partitioned files)
      ↓
Apache Airflow (orchestrates pipeline on monthly schedule)
      ↓
BigQuery (olist_raw → olist_staging → olist_intermediate → olist_marts)
      ↓
dbt Core (transforms and models data, runs tests)
      ↓
Streamlit (interactive dashboard)
```

## Tech Stack

| Layer | Tool |
|---|---|
| Source | Kaggle Olist Dataset |
| Ingestion | Python + pandas |
| Cloud Storage | Google Cloud Storage |
| Infrastructure | Terraform |
| Orchestration | Apache Airflow (Docker Compose) |
| Warehouse | BigQuery |
| Transformation | dbt Core |
| Dashboard | Streamlit + Plotly |
| Reproducible Env | GitHub Codespaces |

## Project Structure

```
e-commerce-data-pipeline/
    .devcontainer/          # GitHub Codespaces config
    ingestion/              # Ingestion scripts
    airflow/                # Airflow DAGs and Docker Compose
    dbt/                    # dbt project (profiles.yml + olist/)
        olist/
            models/
                staging/    # 9 staging models (views)
                intermediate/  # int_orders_enriched
                marts/      # fct_orders, fct_seller_revenue, fct_delivery_by_state
    terraform/              # GCP infrastructure as code (platform.tf = raw_* and platform_* datasets)
    src/ecomplatform/       # ingestion, reconciliation, monitoring, simulators (omnichannel platform)
    configs/                # sources, reconciliation rules, freshness, data-quality YAML
    tests/                  # unit + integration tests (pytest)
    docs/                   # architecture, rules, data dictionary, runbook, deployment
    streamlit_app/          # Dashboard app
```

## Prerequisites

- GitHub account with Codespaces access
- GCP account with billing enabled
- Kaggle account with API access

## Setup

### 1. Fork and add Codespaces secrets

Fork this repo. Before opening in Codespaces, go to **Settings → Secrets and variables → Codespaces** and add:

| Secret | Description |
|---|---|
| `GCP_SERVICE_ACCOUNT_KEY` | GCP service account key JSON |
| `GCS_BUCKET_NAME` | GCS bucket name |
| `GCP_PROJECT_ID` | GCP project ID |
| `KAGGLE_USERNAME` | Kaggle username |
| `KAGGLE_KEY` | Kaggle API key |

### 2. Open in Codespaces

Open the forked repo in GitHub Codespaces. The dev container will automatically:
- Install all Python dependencies (including dbt)
- Install Terraform and Docker
- Create the .gcp directory within the airflow/ folder and write the GCP service account keyfile to airflow/.gcp/keyfile.json

### 3. Provision infrastructure

```bash
cd terraform
terraform init
terraform plan
terraform apply
```

This creates:
- GCS bucket for raw data
- BigQuery datasets: `olist_raw`, `olist_staging`, `olist_intermediate`, `olist_marts`

### 4. Run ingestion

```bash
python ingestion/partition_and_upload.py
```

This downloads the Olist dataset from Kaggle, partitions transactional files by month, and uploads all files to GCS as Parquet.

### 5. Start Airflow

```bash
cd airflow
docker compose up -d
```

Access the Airflow UI at `http://localhost:8080` (username: `admin`, password: `admin`).

### 6. Trigger DAGs

Run in this order:

1. **`dag_load_static`** — loads static dimension tables once (customers, sellers, products, order items, payments, geolocation). These never change and are not reprocessed.
2. **`dag_gcs_to_bq`** — runs monthly, loads partitioned `orders` and `order_reviews` tables, then automatically triggers `dbt build --select stg_orders+ stg_order_reviews` to transform and test only the models affected by new data.

> **Note:** After running `dag_load_static` for the first time, manually run `dbt build --select stg_customers stg_sellers stg_products stg_order_items stg_order_payments stg_geolocation stg_category_translation --target prod` once to populate static staging models in prod.

### 7. Run the dashboard

```bash
cd streamlit_app
streamlit run app.py
```

Access the dashboard at `http://localhost:8501`.

## Dashboard Preview

![Dashboard](docs/dashboard_screenshot.png)

## Data Flow

### GCS Structure

```
gs://<your-bucket>/
    raw/
        orders/
            orders_2016-09.parquet
            orders_2016-10.parquet
            ...
        order_reviews/
            order_reviews_2016-10.parquet
            ...
        static/
            customers.parquet
            sellers.parquet
            products.parquet
            order_items.parquet
            order_payments.parquet
            geolocation.parquet
            category_translation.parquet
```

### BigQuery Datasets

| Dataset | Description |
|---|---|
| `olist_raw` | Raw data loaded directly from GCS |
| `olist_staging` | Cleaned and typed models — one per source table (views) |
| `olist_intermediate` | Joined and enriched models — `int_orders_enriched` |
| `olist_marts` | Business-level fact tables for dashboards |

### dbt Models

**Staging (9 models, views)** — light cleaning and type casting on top of raw tables. One model per source.

**Intermediate (1 model)** — `int_orders_enriched` joins orders with customers and computes delivery time in days.

**Marts (3 models, tables):**

| Model | Materialization | Description |
|---|---|---|
| `fct_orders` | Incremental (insert_overwrite), partitioned by month | Row-level orders with revenue. Powers monthly order and revenue trends. |
| `fct_delivery_by_state` | Table, clustered by state | Delivery time per order per state. Powers avg delivery time KPI. |
| `fct_seller_revenue` | Table, clustered by seller_id | Revenue per order item per seller. Powers top 10 sellers KPI. |

**Data Quality**: The project includes 28 dbt tests (schema tests, uniqueness, and relationship checks) that run as part of the dbt build command in Airflow to ensure data integrity before it reaches the Marts.

### KPIs

- **Total orders and revenue by month** — sourced from `fct_orders`
- **Average delivery time by state** — sourced from `fct_delivery_by_state`
- **Top 10 sellers by revenue** — sourced from `fct_seller_revenue`

## Pipeline Design Decisions

**Idempotency** — `dag_gcs_to_bq` deletes existing rows for the execution month before loading, making every DAG run safe to re-trigger without duplicating data.

**Incremental models** — `fct_orders` uses dbt's `insert_overwrite` incremental strategy, processing only the current month's partition on each run. Static dimension models are built once and not reprocessed.

**Storage Optimization**:
* **Partitioning**: `fct_orders` is partitioned by `order_purchase_timestamp` (monthly), enabling dbt's `insert_overwrite` strategy to replace data at the partition level, significantly reducing processing costs during backfills.
* **Clustering**: High-cardinality columns like `seller_id` and `state` are used for clustering in Marts to optimize filter performance for the Streamlit dashboard.

**Selective dbt execution** — Airflow runs `dbt build --select stg_orders+ stg_order_reviews` instead of rebuilding all models, avoiding unnecessary processing of static staging models on every monthly run.

## Omnichannel Platform extension (ingestion · reconciliation · monitoring)

On top of the Olist batch pipeline, the repo now contains a production-style platform for multi-source e-commerce data.
**All sources are simulated** (FastAPI mock marketplace / OMS / POS / finance APIs with injected, labelled discrepancies) - none is a real
Shopify/TikTok Shop integration.

| Capability | What it does | Code |
|---|---|---|
| Ingestion framework | bearer auth, cursor + page pagination, 429/5xx retry with backoff, incremental watermark with overlap window, schema validation + quarantine, idempotent (id, hash) sink, crash-safe checkpoints | `src/ecomplatform/ingestion` |
| Warehouse models | raw (append-only versions) → staging → unified order → `fct_*`/`dim_*`/`mart_daily_sales`/`mart_financial_reconciliation`; partition + cluster on BigQuery | `dbt/olist/models/platform` |
| Financial reconciliation | 13 config-driven rules (order, payment, refund, settlement), TIMING vs CONFIRMED, exception lifecycle + history, no duplicate exceptions | `src/ecomplatform/reconciliation`, `configs/reconciliation_rules.yaml` |
| Monitoring | `pipeline_run_audit`, freshness, data quality, reliability metrics, **Lark webhook** alerts with grouping, cooldown, escalation and recovery notices | `src/ecomplatform/monitoring` |
| Orchestration | 7 Airflow DAGs + failure callback; Docker Compose adds PostgreSQL metadata DB and the simulator | `airflow/dags/dag_platform.py` |

### Try it in 2 minutes (no cloud account)

```bash
pip install -e ".[sim,local,dev]"
make test     # 76 tests: unit + simulator/DuckDB/dbt + BigQuery fake-client/compile checks (also run in CI, incl. PostgreSQL)
make demo     # simulator -> ingest -> dbt on DuckDB -> reconcile -> freshness/DQ -> alerts
```

What the tests prove (not just exercise): the rules find **exactly** the 30 injected discrepancies with no false positives; re-running
a batch adds 0 rows; an outage mid-pagination leaves the checkpoint untouched and the retry recovers without duplicates; API tokens never
appear in audit rows or alerts; the dbt SQL mart and the Python rule engine flag the same orders; repeated reconciliation runs create no
duplicate exceptions; repeated alerts are grouped and recovery is announced.

**Dashboard** (`streamlit_app/platform_dashboard.py`): a business-readable report on top of the BigQuery views - an auto-written "short story" (what happened / why it matters / what to do), KPI cards with sparklines, activity chart, health rankings, leaderboards, plain-language names for every technical code, a *How to read this* page, and a Vietnamese / English switch.
```bash
pip install -e ".[dashboard]"
# 1) run sql/bigquery_dashboard_views.sql once in the BigQuery console   2) gcloud auth application-default login
GCP_PROJECT_ID=YOUR_PROJECT_ID python -m streamlit run streamlit_app/platform_dashboard.py
# offline demo: DASH_DUCKDB_PATH=.demo/warehouse.duckdb python -m streamlit run streamlit_app/platform_dashboard.py
```
| Overview | Money matching |
|---|---|
| ![Overview](docs/img/dashboard_overview.png) | ![Money matching](docs/img/dashboard_money_matching.png) |

*(screenshots from the offline DuckDB demo; simulated data)*

Run on your own free BigQuery: [quickstart](docs/bigquery_free_quickstart.md).
Docs: [architecture](docs/architecture.md) · [reconciliation rules](docs/reconciliation_rules.md) · [data dictionary](docs/data_dictionary.md) ·
[runbook](docs/operational_runbook.md) · [deployment](docs/deployment.md) · [benchmarks](docs/benchmarks.md)

> **BigQuery verification without a GCP project:** the sink/reader are driven through a fake `bigquery.Client` (call order, arguments, cleanup),
> every SQL statement is parsed with the BigQuery dialect, and all platform dbt models are compiled for the BigQuery target and parsed too
> (datasets, partitioning and clustering asserted). The metadata layer runs on real PostgreSQL in CI.
> **Still needs one real run on GCP:** actual BigQuery execution (permissions, quotas, load/MERGE behaviour), Terraform apply, the Docker Compose stack.
> Benchmarks are intentionally empty until measured.

## Status

- [x] Ingestion layer
- [x] Infrastructure (Terraform)
- [x] Airflow DAGs
- [x] Raw data in BigQuery
- [x] dbt models (staging, intermediate, marts)
- [x] Streamlit dashboard
- [x] Omnichannel platform: ingestion framework, reconciliation, monitoring/Lark alerts, platform dbt models, DAGs, CI
- [ ] Not built yet: inventory source/`fct_inventory_snapshots`, `dim_product`, line-item facts

## Acknowledgments

* **Data Source**: A special thanks to [Olist](https://olist.com/) for releasing this dataset and to [Kaggle](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce) for hosting it.
* **Community**: This project was developed as part of the [Data Engineering Zoomcamp](https://github.com/DataTalksClub/data-engineering-zoomcamp) provided by DataTalks.Club.