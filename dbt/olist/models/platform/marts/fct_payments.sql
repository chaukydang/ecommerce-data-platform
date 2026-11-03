{{ config(materialized='table',
          partition_by=({'field': 'paid_at', 'data_type': 'timestamp', 'granularity': 'day'} if target.type == 'bigquery' else none),
          cluster_by=(['order_id'] if target.type == 'bigquery' else none)) }}
select payment_id, order_id, payment_ref, amount, currency, paid_at,
       count(*) over (partition by payment_ref) > 1 as is_duplicate_ref
from {{ ref('stg_payments') }}
