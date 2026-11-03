{{ config(
    materialized='table',
    cluster_by=['state']
) }}

select state,
       days_to_delivery
from {{ ref('int_orders_enriched') }}
