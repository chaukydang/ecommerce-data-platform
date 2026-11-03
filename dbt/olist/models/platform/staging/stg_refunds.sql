select refund_id, order_id, refund_ref, amount, currency, status as refund_status, refunded_at,
       updated_at, ingested_at, source_system
from {{ latest_version(source('raw_finance', 'refunds')) }}
