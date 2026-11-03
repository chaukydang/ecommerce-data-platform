select payment_id, order_id, payment_ref, amount, currency, paid_at, updated_at, ingested_at, source_system
from {{ latest_version(source('raw_finance', 'payments')) }}
