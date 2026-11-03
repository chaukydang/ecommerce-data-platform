select s.settlement_id, s.order_id, s.channel, s.gross_amount, s.seller_discount, s.refund_amount,
       s.commission_fee, s.shipping_fee, s.adjustment_amount, s.net_payout, s.settled_at,
       round({{ expected_net_payout('s') }}, 2) as expected_net_payout
from {{ ref('stg_settlements') }} as s
