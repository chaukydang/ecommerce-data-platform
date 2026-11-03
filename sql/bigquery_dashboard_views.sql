-- =====================================================================================
-- Reporting views for the Omnichannel E-commerce Data Platform (SIMULATED data)
-- Project: ecommerce-data-platform-513615   |   Location: US
-- Run the whole file in the BigQuery console (Compose new query -> paste -> Run).
-- Creates dataset `platform_reporting` with 7 views for Looker Studio. Safe to re-run.
-- Sandbox note: CREATE VIEW is DDL (allowed); views expire after 60 days in the sandbox.
-- =====================================================================================

CREATE SCHEMA IF NOT EXISTS `ecommerce-data-platform-513615.platform_reporting`
OPTIONS (location = 'US', description = 'Looker Studio reporting views (simulated data)');

-- ---------- PAGE 1: SALES OVERVIEW ------------------------------------------------------

-- Daily sales per channel with rates (use for time series, channel split)
CREATE OR REPLACE VIEW `ecommerce-data-platform-513615.platform_reporting.v_sales_daily` AS
SELECT
  order_date,
  channel,
  orders,
  cancelled_orders,
  SAFE_DIVIDE(cancelled_orders, orders)                         AS cancel_rate,
  gross_sales,
  refunded_amount,
  net_sales,
  SAFE_DIVIDE(refunded_amount, gross_sales)                     AS refund_rate,
  SAFE_DIVIDE(gross_sales, NULLIF(orders - cancelled_orders, 0)) AS avg_order_value
FROM `ecommerce-data-platform-513615.platform_marts.mart_daily_sales`;

-- One-row KPI scorecard for the whole period
CREATE OR REPLACE VIEW `ecommerce-data-platform-513615.platform_reporting.v_sales_kpi` AS
SELECT
  SUM(orders)                                                   AS orders,
  SUM(cancelled_orders)                                         AS cancelled_orders,
  SAFE_DIVIDE(SUM(cancelled_orders), SUM(orders))               AS cancel_rate,
  SUM(gross_sales)                                              AS gross_sales,
  SUM(refunded_amount)                                          AS refunded_amount,
  SUM(net_sales)                                                AS net_sales,
  SAFE_DIVIDE(SUM(gross_sales), SUM(orders - cancelled_orders)) AS avg_order_value,
  MIN(order_date)                                               AS first_order_date,
  MAX(order_date)                                               AS last_order_date
FROM `ecommerce-data-platform-513615.platform_marts.mart_daily_sales`;

-- ---------- PAGE 2: FINANCIAL RECONCILIATION -------------------------------------------

-- Long format: one row per order x check (OK / N/A rows kept so counts add up).
-- amount_difference is only set for amount-type findings (same definition as the Python engine's 'unresolved difference').
CREATE OR REPLACE VIEW `ecommerce-data-platform-513615.platform_reporting.v_recon_checks` AS
WITH base AS (
  SELECT * FROM `ecommerce-data-platform-513615.platform_marts.mart_financial_reconciliation`
),
unpivoted AS (
  SELECT order_id, channel, order_status, created_at, 'Order vs OMS'        AS check_type, oms_check        AS result,
         0.0 AS amount_difference FROM base
  UNION ALL
  SELECT order_id, channel, order_status, created_at, 'Payment',            payment_check,
         IF(payment_check = 'AMOUNT_MISMATCH', ABS(total_amount - IFNULL(paid_amount, 0)), 0) FROM base
  UNION ALL
  SELECT order_id, channel, order_status, created_at, 'Currency',           currency_check,        0 FROM base
  UNION ALL
  SELECT order_id, channel, order_status, created_at, 'Refund',             refund_check,
         IF(refund_check = 'REFUND_EXCEEDS_PAID', IFNULL(refunded_amount, 0) - IFNULL(paid_amount, 0), 0) FROM base
  UNION ALL
  SELECT order_id, channel, order_status, created_at, 'Settlement',         settlement_check,
         IF(settlement_check = 'FORMULA_MISMATCH', ABS(net_payout - expected_net_payout), 0) FROM base
)
SELECT
  order_id, channel, order_status, created_at, check_type, result,
  CASE
    WHEN result IN ('OK', 'N/A')               THEN 'OK'
    WHEN STARTS_WITH(result, 'PENDING')         THEN 'TIMING'     -- inside grace window: expected sync lag
    ELSE 'CONFIRMED'                                              -- confirmed financial finding
  END AS finding_class,
  IF(result IN ('OK', 'N/A') OR STARTS_WITH(result, 'PENDING'), 0, amount_difference) AS confirmed_amount_difference
FROM unpivoted;

-- Findings only (table for the "exceptions" list on the dashboard)
CREATE OR REPLACE VIEW `ecommerce-data-platform-513615.platform_reporting.v_recon_findings` AS
SELECT order_id, channel, order_status, created_at, check_type, result AS finding,
       finding_class, ROUND(confirmed_amount_difference, 2) AS amount_difference
FROM `ecommerce-data-platform-513615.platform_reporting.v_recon_checks`
WHERE finding_class IN ('CONFIRMED', 'TIMING');

-- Findings by check type and result (bar chart / heatmap)
CREATE OR REPLACE VIEW `ecommerce-data-platform-513615.platform_reporting.v_recon_summary` AS
SELECT
  check_type, result, finding_class,
  COUNT(*)                                   AS orders,
  ROUND(SUM(confirmed_amount_difference), 2) AS amount_difference
FROM `ecommerce-data-platform-513615.platform_reporting.v_recon_checks`
WHERE result <> 'N/A'
GROUP BY check_type, result, finding_class;

-- One-row KPI scorecard
CREATE OR REPLACE VIEW `ecommerce-data-platform-513615.platform_reporting.v_recon_kpi` AS
WITH per_order AS (
  SELECT order_id,
         LOGICAL_OR(finding_class = 'CONFIRMED') AS has_confirmed,
         LOGICAL_OR(finding_class = 'TIMING')    AS has_timing
  FROM `ecommerce-data-platform-513615.platform_reporting.v_recon_checks`
  GROUP BY order_id
),
totals AS (
  SELECT
    COUNT(*)                                       AS orders_checked,
    COUNTIF(has_confirmed)                         AS orders_with_confirmed_issue,
    COUNTIF(has_timing AND NOT has_confirmed)      AS orders_with_timing_only
  FROM per_order
),
findings AS (
  SELECT
    COUNTIF(finding_class = 'CONFIRMED')           AS confirmed_findings,
    COUNTIF(finding_class = 'TIMING')              AS timing_findings,
    ROUND(SUM(confirmed_amount_difference), 2)     AS unresolved_amount_difference
  FROM `ecommerce-data-platform-513615.platform_reporting.v_recon_checks`
)
SELECT
  t.orders_checked,
  t.orders_with_confirmed_issue,
  t.orders_with_timing_only,
  ROUND(100 * SAFE_DIVIDE(t.orders_checked - t.orders_with_confirmed_issue, t.orders_checked), 2) AS clean_orders_pct,
  f.confirmed_findings,
  f.timing_findings,
  f.unresolved_amount_difference
FROM totals t CROSS JOIN findings f;

-- ---------- PAGE 3: PIPELINE HEALTH (derived from the raw layer) ------------------------

-- Per raw table: volume, versions, freshness. (Full audit/retry history lives in the operational DB.)
CREATE OR REPLACE VIEW `ecommerce-data-platform-513615.platform_reporting.v_pipeline_health` AS
WITH raw_stats AS (
  SELECT 'raw_marketplace.orders' AS dataset, source_record_id, ingested_at, source_updated_at, batch_id
  FROM `ecommerce-data-platform-513615.raw_marketplace.orders`
  UNION ALL SELECT 'raw_oms.orders',           source_record_id, ingested_at, source_updated_at, batch_id
  FROM `ecommerce-data-platform-513615.raw_oms.orders`
  UNION ALL SELECT 'raw_pos.transactions',     source_record_id, ingested_at, source_updated_at, batch_id
  FROM `ecommerce-data-platform-513615.raw_pos.transactions`
  UNION ALL SELECT 'raw_finance.payments',     source_record_id, ingested_at, source_updated_at, batch_id
  FROM `ecommerce-data-platform-513615.raw_finance.payments`
  UNION ALL SELECT 'raw_finance.refunds',      source_record_id, ingested_at, source_updated_at, batch_id
  FROM `ecommerce-data-platform-513615.raw_finance.refunds`
  UNION ALL SELECT 'raw_finance.settlements',  source_record_id, ingested_at, source_updated_at, batch_id
  FROM `ecommerce-data-platform-513615.raw_finance.settlements`
)
SELECT
  dataset,
  COUNT(*)                                         AS rows_loaded,
  COUNT(DISTINCT source_record_id)                 AS distinct_records,
  COUNT(*) - COUNT(DISTINCT source_record_id)      AS extra_versions,        -- updates kept as history
  COUNT(DISTINCT batch_id)                         AS batches,
  MIN(ingested_at)                                 AS first_loaded_at,
  MAX(ingested_at)                                 AS last_loaded_at,
  MAX(source_updated_at)                           AS latest_source_update,
  TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), MAX(ingested_at), MINUTE) AS minutes_since_last_load
FROM raw_stats
GROUP BY dataset;
