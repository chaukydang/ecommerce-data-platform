{#- Warehouse-native twin of the Python rule engine, for BI. Grain: one order.
    *_check values: OK | N/A | PENDING_* (timing difference, inside the grace window) | <confirmed finding>. -#}
{% set sync_cut = recon_cutoff(2) %}
{% set settle_cut = recon_cutoff(168) %}
{% set tol = tolerance() %}
{% set has_oms = var('has_oms', true) %}
{% set has_settlements = var('has_settlements', true) %}
select u.order_id, u.channel, u.order_status, u.total_amount, u.created_at,
    {% if has_oms %}    case
        when u.oms_order_id is null and u.order_status = 'UNPAID' then 'OK'
        when u.oms_order_id is null then case when u.created_at <= {{ sync_cut }} then 'MISSING_IN_OMS' else 'PENDING_SYNC' end
        when abs(round(u.oms_amount - u.total_amount, 2)) > {{ tol }} then case when u.created_at <= {{ sync_cut }} then 'AMOUNT_MISMATCH' else 'PENDING_SYNC' end
        else 'OK' end as oms_check{% else %}'N/A' as oms_check{% endif %},
    case
        when u.order_status not in ('PAID', 'SHIPPED', 'COMPLETED') then 'N/A'
        when u.payment_count is null then case when u.created_at <= {{ sync_cut }} then 'MISSING' else 'PENDING_SYNC' end
        when abs(round(u.paid_amount - u.total_amount, 2)) > {{ tol }} then case when u.created_at <= {{ sync_cut }} then 'AMOUNT_MISMATCH' else 'PENDING_SYNC' end
        else 'OK' end as payment_check,
    case when u.payment_count is not null and u.payment_currency <> u.currency then 'CURRENCY_MISMATCH' else 'OK' end as currency_check,
    case
        when u.refund_count is null then 'N/A'
        when u.payment_count is null then case when u.last_refunded_at <= {{ sync_cut }} then 'REFUND_WITHOUT_PAYMENT' else 'PENDING_SYNC' end
        when round(u.refunded_amount - u.paid_amount, 2) > {{ tol }} then 'REFUND_EXCEEDS_PAID'
        else 'OK' end as refund_check,
    {% if has_settlements %}    case
        when u.settlement_id is null and u.order_status = 'COMPLETED' then case when u.updated_at <= {{ settle_cut }} then 'MISSING' else 'PENDING_SETTLEMENT' end
        when u.settlement_id is null then 'N/A'
        when abs(round(u.net_payout - u.expected_net_payout, 2)) > {{ tol }} then 'FORMULA_MISMATCH'
        when abs(round(u.settlement_gross - u.total_amount, 2)) > {{ tol }} then 'GROSS_MISMATCH'
        else 'OK' end as settlement_check{% else %}'N/A' as settlement_check{% endif %},
    u.paid_amount, u.refunded_amount, u.net_payout, u.expected_net_payout
from {{ ref('int_unified_orders') }} as u
