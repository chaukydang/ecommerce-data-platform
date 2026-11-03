# Real data: connect a Shopify development store

The `shopify` source reads **orders, payments (transactions) and refunds** from the Shopify Admin REST API into `raw_shopify.*`, then runs
the same dbt models, reconciliation, monitoring and dashboard as the simulated platform - in separate datasets so real and simulated
data never mix (`raw_shopify`, `platform_*_shopify`, `platform_reporting_shopify`).

What is *not* available for a Shopify store (so those checks show "N/A" instead of fake errors): OMS, POS, marketplace settlements
(development stores have no Shopify Payments payouts).

## 1. Create the store and the app (one time, free)

Since 1 Jan 2026 Shopify no longer lets you create "legacy custom apps" with a fixed token; apps live in the **Dev Dashboard** and use
the *client credentials grant* (token valid 24 h - the connector fetches and refreshes it automatically).

1. Create a Shopify Partner account (https://www.shopify.com/partners) and open the Dev Dashboard (https://dev.shopify.com/dashboard).
2. **Stores → Add development store** (choose the option to generate test data if offered).
3. **Apps → Create app** (same organization as the store). In the app version, set the Admin API scope **`read_orders`**
   (add `read_all_orders` if you need orders older than 60 days), release the version, and **install the app on your development store**.
4. In the app **Settings**, copy the **Client ID** and **Client secret**.
5. Make some data: in the store admin create a handful of products and orders (Orders → Create order → *Mark as paid*), plus one
   pending order, one cancelled order and one partial refund, so the dashboard has variety.

## 2. Run

```bash
export SHOPIFY_STORE_URL=https://YOUR-STORE.myshopify.com
export SHOPIFY_CLIENT_ID=...            # from Dev Dashboard
export SHOPIFY_CLIENT_SECRET=...
# alternative with an existing static token: export SHOPIFY_ACCESS_TOKEN=...
# optional: export SHOPIFY_API_VERSION=2026-01

gcloud auth application-default login   # once, for BigQuery
python scripts/run_profile.py --profile shopify --backend bigquery --project YOUR_PROJECT_ID --location US
# offline warehouse file instead of BigQuery: python scripts/run_profile.py --profile shopify --backend duckdb
```

Then the dashboard (the command is also printed at the end of the run):

```bash
DASH_DATASET=platform_reporting_shopify DASH_SIMULATED=0 DASH_SOURCE_LABEL="Shopify test store" GCP_PROJECT_ID=YOUR_PROJECT_ID BQ_LOCATION=US \
  python -m streamlit run streamlit_app/platform_dashboard.py
```

The run: creates empty stub tables for the missing sources → ingests orders/payments/refunds incrementally → `dbt build`
(vars `platform_suffix=_shopify`, `has_oms=false`, `has_settlements=false`) → creates the reporting views → reconciles payment and
refund rules (OMS/settlement rules are disabled in `configs/reconciliation_rules_shopify.yaml`) → freshness + data quality.
Run it again any time: loads are incremental and idempotent.

Keep secrets out of the repo: use environment variables only (`.env` is git-ignored). The client secret never appears in logs, the audit
table or alerts.

## 3. What the connector does

| Entity | Shopify endpoint | Mapping |
|---|---|---|
| orders | `GET /admin/api/{version}/orders.json?status=any&updated_at_min=…` (Link-header pagination) | `SHP-<id>`; status from `financial_status`/`fulfillment_status`/`cancelled_at`; amounts are strings → numbers |
| payments | `GET /orders/{id}/transactions.json` (one call per order in the window) | successful `sale`/`capture` transactions |
| refunds | embedded `refunds[]` of the order | sum of successful `refund` transactions |

Store-local timestamps (`+07:00`) are converted to UTC. HTTP 429 (2 calls/second leaky bucket) is retried with `Retry-After`; an
expired token is refreshed once; pages are committed as they arrive and the checkpoint only advances after the whole run succeeded.

## 4. Known limits

* Shopify calls the REST Admin API *legacy* (GraphQL is the long-term API). Custom apps may still use REST; a GraphQL connector would be the next step.
* Payments need one request per order, fine for a development store, slow for very large stores (use GraphQL bulk operations there).
* The Shopify connector is tested against a **local mock of the API shape** (`src/ecomplatform/simulators/shopify_mock.py`); a run against
  your real store is the first real-world test - send the log if something differs.

## 5. Troubleshooting

| Symptom | Fix |
|---|---|
| `Shopify token request failed (HTTP 4xx)` | wrong client id/secret, app not installed on that store, or app and store are in different organizations |
| `HTTP 401/403` on orders | missing `read_orders` scope (release a new app version and re-install) |
| `extracted=0` | no orders updated in the window, or orders are older than 60 days without `read_all_orders` |
| `HTTP 404` on the API path | set `SHOPIFY_API_VERSION` to a currently supported version |
| dbt `Not found: Dataset ...` | run in the same `--location` for every step |
