# Reconciliation rules

Rules live in [`configs/reconciliation_rules.yaml`](../configs/reconciliation_rules.yaml); adding a rule of an existing type needs no code.

| Rule | Type | Checks | Severity |
|---|---|---|---|
| `ORD_MISSING_IN_OMS` | missing_in_target | marketplace order (not UNPAID) absent in OMS after 2 h | CRITICAL |
| `OMS_DUPLICATE_ORDER` | duplicate_key | more than one OMS record per marketplace order | WARNING |
| `ORD_AMOUNT_MISMATCH` | amount_mismatch | marketplace vs OMS total beyond tolerance | WARNING |
| `PAY_MISSING` | missing_in_target | PAID/SHIPPED/COMPLETED order without payment after 2 h | CRITICAL |
| `PAY_AMOUNT_MISMATCH` | amount_mismatch | order total vs Σ payments (duplicate refs counted once): partial / over payment | CRITICAL |
| `PAY_DUPLICATE_REF` | duplicate_key | same `payment_ref` more than once | WARNING |
| `PAY_CURRENCY_MISMATCH` | field_mismatch | payment currency ≠ order currency | CRITICAL |
| `REF_WITHOUT_PAYMENT` | missing_in_target | refund for an order with no payment | CRITICAL |
| `REF_EXCEEDS_PAID` | sum_exceeds | Σ refunds > Σ payments + tolerance | CRITICAL |
| `REF_DUPLICATE_REF` | duplicate_key | same `refund_ref` more than once | WARNING |
| `SETTLE_MISSING` | missing_in_target | COMPLETED order without settlement after 7 days | WARNING |
| `SETTLE_FORMULA_MISMATCH` | settlement_formula | `net_payout` ≠ Σ coefficient × component (per channel) | CRITICAL |
| `SETTLE_GROSS_MISMATCH` | amount_mismatch | settlement gross ≠ order total | CRITICAL |

## Rule types

`missing_in_target`, `duplicate_key`, `amount_mismatch` (optional `right_agg: sum`, `right_dedupe`, `where`), `field_mismatch`,
`sum_exceeds`, `settlement_formula`. Each rule may set `tolerance_abs`, `severity`, `where` (pandas query on the left table),
and `time_col` + `grace_hours` for timing classification.

## Timing vs confirmed

If the entity's `time_col` is newer than `as_of − grace_hours` the finding is **TIMING** (expected lag; auto-resolves when the
other side arrives). Otherwise it is **CONFIRMED**. Alerts, `unresolved_confirmed_amount()` and the CRITICAL escalation use CONFIRMED only.

## Settlement formula

```yaml
settlement_formulas:
  default: {gross_amount: 1, seller_discount: -1, refund_amount: -1, commission_fee: -1, shipping_fee: -1, adjustment_amount: 1}
  <channel_name>: {...}      # override per marketplace
```
The default is a **placeholder**: configure each marketplace's real accounting definition (who funds discounts/subsidies, how
shipping and VAT on fees are treated) before using it for finance sign-off. The dbt macro `expected_net_payout` mirrors the default.

## Exception lifecycle

`exception_id = hash(rule_id, entity_id)` → re-running never duplicates.

```
            detected                     condition gone                  set_status()
 (none) ───────────► OPEN ◄───────────────────────────────┐   OPEN ─► INVESTIGATING ─► ACCEPTED_EXCEPTION
                      │  ▲ reappears                      │
                      ▼  │                                │   ACCEPTED_EXCEPTION is a human decision:
                   RESOLVED (kept, resolved_at set) ◄─────┘   never auto-resolved or overridden.
```
Every transition is written to `reconciliation_exception_history` (DETECTED, UPDATED, CLASS_CHANGED, REOPENED, AUTO_RESOLVED, STATUS_CHANGED).

## Investigation queries (PostgreSQL)

```sql
-- what is open and confirmed, biggest first
select rule_id, entity_id, expected_value, actual_value, difference_amount, detected_at
from reconciliation_exceptions
where exception_status in ('OPEN','INVESTIGATING') and exception_class = 'CONFIRMED'
order by abs(difference_amount) desc nulls last;

-- full audit trail of one exception
select * from reconciliation_exception_history where exception_id = :id order by at;

-- matched % per run (success metric)
select reconciliation_run_id, as_of, round(100.0*records_matched/nullif(records_checked,0),2) as matched_pct,
       confirmed_exceptions, timing_exceptions, confirmed_difference_amount
from reconciliation_runs order by started_at desc;
```
