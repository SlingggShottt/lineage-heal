{#
    The rows of a bronze model that come from the latest stored version of each day's file.

    A day's file can be republished, and bronze keeps every version. The latest version is the
    one that arrived last (brz_meta__raw_file_versions.arrived_at). Bronze models name the
    file a row came from in _source_file, whose "<16 hex>__" prefix is the version.

    A day with no entry in the versions model keeps all of its rows rather than losing them,
    so a gap shows up as duplicate keys in the uniqueness tests instead of as missing data.

    Arguments: the bronze relation, and the source and dataset names used in the raw zone.
#}
{% macro latest_version_rows(bronze, source, dataset) -%}
(
    with candidates as (
        select
            *,
            regexp_extract(_source_file, '([0-9a-f]{16})__', 1) as _file_version
        from {{ bronze }}
    ),

    latest as (
        select
            trade_date,
            version
        from {{ ref('brz_meta__raw_file_versions') }}
        where source = '{{ source }}' and dataset = '{{ dataset }}'
        qualify row_number() over (
            partition by trade_date order by arrived_at desc, version desc
        ) = 1
    )

    select candidates.*
    from candidates
    left join latest on candidates._trade_date = latest.trade_date
    where latest.version is null or candidates._file_version = latest.version
)
{%- endmacro %}
