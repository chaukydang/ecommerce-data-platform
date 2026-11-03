with source as (
    select * from {{ source('olist_raw', 'orders') }}
),

renamed as (
    select
        order_id,
        customer_id,
        order_status,
        order_purchase_timestamp     as purchased_at,
        order_approved_at            as approved_at,
        order_delivered_carrier_date as delivered_to_carrier_at,
        order_delivered_customer_date as delivered_to_customer_at,
        order_estimated_delivery_date as estimated_delivery_at
    from source
    where order_id is not null
)

select * from renamed
