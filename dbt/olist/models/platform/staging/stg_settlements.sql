select settlement_id, order_id, channel, gross_amount, seller_discount, refund_amount, commission_fee,
       shipping_fee, adjustment_amount, net_payout, currency, settled_at, updated_at, ingested_at, source_system
from {{ latest_version(source('raw_finance', 'settlements')) }}
