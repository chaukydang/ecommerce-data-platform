with source as (
    select * from {{ source('olist_raw', 'geolocation') }}
),

renamed as (
    select
        geolocation_zip_code_prefix as zip_code_prefix,
        geolocation_lat             as latitude,
        geolocation_lng             as longitude,
        geolocation_city            as city,
        geolocation_state           as state
    from source
    where geolocation_zip_code_prefix is not null
)

select * from renamed
