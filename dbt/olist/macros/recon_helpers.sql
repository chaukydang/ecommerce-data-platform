{#- "As of" moment for timing-vs-confirmed classification. Pass --vars '{as_of: "2026-11-03 10:00:00"}' to pin it. -#}
{% macro recon_cutoff(hours) -%}
    {%- set fmt = '%Y-%m-%d %H:%M:%S' -%}
    {%- set as_of = modules.datetime.datetime.strptime(var('as_of'), fmt) if var('as_of', none) else modules.datetime.datetime.utcnow() -%}
    {%- set cut = as_of - modules.datetime.timedelta(hours=hours) -%}
    timestamp '{{ cut.strftime(fmt) }}'
{%- endmacro %}

{% macro tolerance() -%}{{ var('recon_tolerance', 0.01) }}{%- endmacro %}

{#- Settlement net payout formula. Keep in sync with settlement_formulas.default in configs/reconciliation_rules.yaml.
    Real marketplaces differ: override per channel before relying on this for finance sign-off. -#}
{% macro expected_net_payout(alias='s') -%}
    ({{ alias }}.gross_amount - {{ alias }}.seller_discount - {{ alias }}.refund_amount - {{ alias }}.commission_fee
     - {{ alias }}.shipping_fee + {{ alias }}.adjustment_amount)
{%- endmacro %}

{% macro date_series(start_expr, end_expr) -%}
    {%- if target.type == 'bigquery' -%}
        select d as date_day from unnest(generate_date_array({{ start_expr }}, {{ end_expr }})) as d
    {%- else -%}
        select cast(d as date) as date_day from generate_series({{ start_expr }}, {{ end_expr }}, interval 1 day) as t(d)
    {%- endif -%}
{%- endmacro %}
