select oms_order_id, order_id, status as order_status, total_amount, currency,
       created_at, updated_at, ingested_at, source_system
from {{ latest_version(source('raw_oms', 'orders')) }}
