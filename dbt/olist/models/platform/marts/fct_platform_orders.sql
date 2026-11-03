{{ config(materialized='table',
          partition_by=({'field': 'created_at', 'data_type': 'timestamp', 'granularity': 'day'} if target.type == 'bigquery' else none),
          cluster_by=(['channel', 'order_status'] if target.type == 'bigquery' else none)) }}
{#- Named fct_platform_orders because fct_orders already exists in the Olist mart. Grain: one canonical order. -#}
select order_id, channel, order_status, total_amount, currency, customer_id, created_at, updated_at,
       oms_order_id is not null as in_oms,
       coalesce(paid_amount, 0) as paid_amount, refunded_amount,
       net_payout, settled_at
from {{ ref('int_unified_orders') }}
