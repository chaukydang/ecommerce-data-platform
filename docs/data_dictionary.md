# Data dictionary & source-to-target mapping

All raw tables carry ingestion metadata: `source_system`, `source_record_id` (business key, composite parts joined by `:`),
`source_updated_at`, `ingested_at`, `batch_id`, `record_hash`. Raw tables are append-only, partitioned by `ingested_at` and clustered by
`source_record_id` in BigQuery. Source = **simulated** API.

## Raw → staging → marts

| Source field (API) | Raw table.column | Normalisation | Staging / mart |
|---|---|---|---|
| marketplace `total_amount_minor` (cents) | `raw_marketplace.orders.total_amount` | ÷ 10^`currency_exponent` | `stg_marketplace_orders.total_amount` → `fct_platform_orders.total_amount` |
| marketplace `status` (UNPAID/PAID/…) | `raw_marketplace.orders.status` | mapped to PENDING/PAID/SHIPPED/COMPLETED/CANCELLED | `order_status` |
| OMS `marketplace_order_id` | `raw_oms.orders.order_id` | renamed | join key to marketplace |
| OMS `fulfillment_status` (RECEIVED/ALLOCATED/…) | `raw_oms.orders.status` | mapped to canonical vocabulary | `stg_oms_orders.order_status` |
| finance `payment_ref` | `raw_finance.payments.payment_ref` | – | duplicates counted once in `int_order_payment_mapping` |
| finance `refund_ref` | `raw_finance.refunds.refund_ref` | – | duplicates counted once in `int_refund_allocations` |
| finance settlement components | `raw_finance.settlements.*` | missing optional components → 0 | `int_settlement_allocations.expected_net_payout` |
| POS `type` | `raw_pos.transactions.txn_type` | upper-cased | `stg_pos_transactions` |

## dbt models (`dbt/olist/models/platform`)

| Model | Layer | Grain |
|---|---|---|
| `stg_marketplace_orders`, `stg_oms_orders`, `stg_payments`, `stg_refunds`, `stg_settlements`, `stg_pos_transactions` | staging (views) | latest version per source record |
| `int_order_payment_mapping` | intermediate | order (Σ distinct payments) |
| `int_refund_allocations` | intermediate | order (Σ distinct refunds) |
| `int_settlement_allocations` | intermediate | settlement (+ expected net payout) |
| `int_unified_orders` | intermediate | canonical order (marketplace + OMS + payment + refund + settlement) |
| `fct_platform_orders` | mart | one order; clustered by channel/status on BigQuery (not partitioned: see architecture.md, sandbox limits) |
| `fct_payments`, `fct_refunds`, `fct_settlements` | mart | one transaction / settlement entry |
| `dim_channel`, `dim_date` | mart | channel / calendar date |
| `mart_daily_sales` | mart | date × channel (gross excludes cancelled; net = gross − refunds) |
| `mart_financial_reconciliation` | mart | one order, with `oms_check`, `payment_check`, `currency_check`, `refund_check`, `settlement_check` |

Not built (no simulated source yet): `fct_inventory_snapshots`, `dim_product`. `fct_order_items` is not built because the simulated orders have no line items.

## Operational tables (PostgreSQL / SQLite)

`ingestion_checkpoints`, `failed_batches`, `pipeline_run_audit` (spec columns + statuses RUNNING/SUCCESS/FAILED/PARTIAL_SUCCESS),
`data_quality_results`, `alert_state`, `reconciliation_runs`, `reconciliation_exceptions` (spec columns + `exception_class`,
`first_run_id`, `last_seen_at`, `details`), `reconciliation_exception_history`. See `src/ecomplatform/db.py`.
