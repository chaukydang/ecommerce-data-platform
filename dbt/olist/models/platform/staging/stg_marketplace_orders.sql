select order_id, channel, status as order_status, total_amount, currency, customer_id,
       created_at, updated_at, ingested_at, source_system
from {{ latest_version(source('raw_marketplace', 'orders')) }}
