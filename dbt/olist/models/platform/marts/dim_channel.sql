select channel as channel_name,
       min(created_at) as first_order_at,
       max(created_at) as last_order_at,
       count(*) as orders_count
from {{ ref('stg_marketplace_orders') }}
group by channel
