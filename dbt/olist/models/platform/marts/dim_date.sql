with bounds as (
    select cast(min(created_at) as date) as d0, cast(max(created_at) as date) as d1
    from {{ ref('stg_marketplace_orders') }}
)
select date_day,
       extract(year from date_day)  as year,
       extract(month from date_day) as month,
       extract(day from date_day)   as day_of_month,
       extract(dayofweek from date_day) as day_of_week
from ({{ date_series("(select d0 from bounds)", "(select d1 from bounds)") }}) as s
