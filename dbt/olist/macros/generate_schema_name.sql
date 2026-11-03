{#- Olist models keep dbt's default `<target dataset>_<custom>` naming (olist_staging, ...).
    Platform models (models/platform/**) use the custom schema as-is, e.g. `platform_marts`. -#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is not none and 'models/platform/' in (node.original_file_path | replace('\\', '/')) -%}
        {{ custom_schema_name | trim }}
    {%- elif custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ target.schema }}_{{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
