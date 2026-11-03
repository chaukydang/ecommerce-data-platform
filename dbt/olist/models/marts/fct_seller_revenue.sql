{{ config(
    materialized='table',
    cluster_by=['seller_id']
) }}

with delivered_orders as (
select order_id
from {{ ref('stg_orders') }}
where order_status = 'delivered'
)

select seller_id,
       item_price as item_gross_revenue
from {{ ref('stg_order_items') }} i
inner join delivered_orders o on i.order_id = o.order_id
