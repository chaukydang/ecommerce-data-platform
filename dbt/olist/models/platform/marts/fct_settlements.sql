select settlement_id, order_id, channel, gross_amount, seller_discount, refund_amount, commission_fee,
       shipping_fee, adjustment_amount, net_payout, expected_net_payout,
       round(net_payout - expected_net_payout, 2) as payout_variance, settled_at
from {{ ref('int_settlement_allocations') }}
