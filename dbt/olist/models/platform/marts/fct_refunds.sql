select refund_id, order_id, refund_ref, amount, currency, refund_status, refunded_at,
       count(*) over (partition by refund_ref) > 1 as is_duplicate_ref
from {{ ref('stg_refunds') }}
