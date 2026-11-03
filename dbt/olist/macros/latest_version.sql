{#- The raw layer keeps every distinct version of a source record (append-only, idempotent loads).
    Staging exposes only the latest version per (source_system, source_record_id). -#}
{% macro latest_version(relation) %}
(
    select * from (
        select r.*,
               row_number() over (
                   partition by r.source_record_id
                   order by r.source_updated_at desc, r.ingested_at desc
               ) as _rn
        from {{ relation }} as r
    ) as _ranked
    where _rn = 1
)
{% endmacro %}
