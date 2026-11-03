with dedup as (
    select *, row_number() over (partition by refund_ref order by refund_id) as _rn
    from {{ ref('stg_refunds') }}
)
select order_id,
       sum(amount)       as refunded_amount,
       count(*)          as refund_count,
       max(refunded_at)  as last_refunded_at
from dedup
where _rn = 1
group by order_id
