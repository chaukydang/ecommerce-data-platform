{{ config(materialized='table',
          partition_by=({'field': 'order_date', 'data_type': 'date'} if target.type == 'bigquery' else none),
          cluster_by=(['channel'] if target.type == 'bigquery' else none)) }}
{#- Grain: one date x channel. Gross sales exclude cancelled orders; net = gross - refunds. -#}
select cast(created_at as date) as order_date,
       channel,
       count(*) as orders,
       sum(case when order_status = 'CANCELLED' then 1 else 0 end) as cancelled_orders,
       sum(case when order_status <> 'CANCELLED' then total_amount else 0 end) as gross_sales,
       sum(refunded_amount) as refunded_amount,
       sum(case when order_status <> 'CANCELLED' then total_amount else 0 end) - sum(refunded_amount) as net_sales
from {{ ref('fct_platform_orders') }}
group by 1, 2
