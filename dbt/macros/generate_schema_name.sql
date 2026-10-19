{#
    By default dbt names a schema <target schema>_<custom schema>, which would give
    names like main_bronze. Here the custom schema is used as is, so models land in
    plain bronze, silver and gold schemas on every target.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
