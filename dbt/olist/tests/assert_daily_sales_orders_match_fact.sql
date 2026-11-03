-- Invariant: every order lands in exactly one date/channel bucket.
select 'mismatch' as problem
from (select sum(orders) as n from {{ ref('mart_daily_sales') }}) a
cross join (select count(*) as n from {{ ref('fct_platform_orders') }}) b
where a.n <> b.n
