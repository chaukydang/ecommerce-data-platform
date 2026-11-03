select * from {{ ref('mart_daily_sales') }} where net_sales > gross_sales + 0.01
