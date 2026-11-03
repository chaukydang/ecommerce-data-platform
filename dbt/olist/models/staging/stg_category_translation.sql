with source as (
    select * from {{ source('olist_raw', 'category_translation') }}
),

renamed as (
    select
        product_category_name           as category_name,
        product_category_name_english   as category_name_english
    from source
    where product_category_name is not null
)

select * from renamed
