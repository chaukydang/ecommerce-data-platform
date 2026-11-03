with orders_enriched as (
select o.order_id,
       o.customer_id,
       o.purchased_at,
       o.delivered_to_customer_at,
       c.state
from {{ ref('stg_orders') }} as o
left join {{ ref('stg_customers') }} as c on c.customer_id = o.customer_id
where o.order_status = 'delivered'
)

select order_id,
       customer_id,
       state,
       date_diff(delivered_to_customer_at, purchased_at, day) as days_to_delivery
from orders_enriched