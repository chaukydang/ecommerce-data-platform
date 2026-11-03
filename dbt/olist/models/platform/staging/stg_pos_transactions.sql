select txn_id, store_id, txn_type, amount, currency, occurred_at, updated_at, ingested_at, source_system
from {{ latest_version(source('raw_pos', 'transactions')) }}
