{#- Canonical order: marketplace order + its OMS record + payments + refunds + settlement. -#}
with oms_one as (
    select *, row_number() over (partition by order_id order by oms_order_id) as _rn
    from {{ ref('stg_oms_orders') }}
),
settle_one as (
    select *, row_number() over (partition by order_id order by settlement_id) as _rn
    from {{ ref('int_settlement_allocations') }}
)
select o.order_id, o.channel, o.order_status, o.total_amount, o.currency, o.customer_id,
       o.created_at, o.updated_at,
       m.oms_order_id, m.order_status as oms_status, m.total_amount as oms_amount,
       p.paid_amount, p.payment_count, p.payment_currency, p.last_paid_at,
       coalesce(r.refunded_amount, 0) as refunded_amount, r.refund_count, r.last_refunded_at,
       s.settlement_id, s.gross_amount as settlement_gross, s.net_payout, s.expected_net_payout, s.settled_at
from {{ ref('stg_marketplace_orders') }} as o
left join oms_one as m on m.order_id = o.order_id and m._rn = 1
left join {{ ref('int_order_payment_mapping') }} as p on p.order_id = o.order_id
left join {{ ref('int_refund_allocations') }} as r on r.order_id = o.order_id
left join settle_one as s on s.order_id = o.order_id and s._rn = 1
