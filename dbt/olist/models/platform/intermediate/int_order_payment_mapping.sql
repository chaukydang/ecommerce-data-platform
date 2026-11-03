{#- One row per order. Duplicate payment references (API retries) are counted once. -#}
with dedup as (
    select *, row_number() over (partition by payment_ref order by payment_id) as _rn
    from {{ ref('stg_payments') }}
)
select order_id,
       sum(amount)             as paid_amount,
       count(*)                as payment_count,
       min(currency)           as payment_currency,
       count(distinct currency) as currency_count,
       max(paid_at)            as last_paid_at
from dedup
where _rn = 1
group by order_id
