{{ config(
    materialized='incremental',
    partition_by={
        'field': 'purchased_at',
        'data_type': 'timestamp',
        'granularity': 'month'
    },
    incremental_strategy='insert_overwrite'
) }}

with order_payments as (
select order_id,
       sum(payment_value) as total_order_revenue
from {{ ref('stg_order_payments') }}
group by 1
),

orders_delivered as (
select purchased_at,
       order_id
from {{ ref('stg_orders') }}
where order_status = 'delivered'

{% if is_incremental() %}
and purchased_at > (select coalesce(max(purchased_at), '1900-01-01') from {{ this }})
{% endif %}
)

select o.purchased_at,
       o.order_id,
       coalesce(p.total_order_revenue, 0) as total_order_revenue
from orders_delivered o
left join order_payments p on o.order_id = p.order_id

