with

-- One version of each day's file: the latest to arrive.
latest as (
    select * from {{ latest_version_rows(ref('brz_fbil__reference_rates'), 'fbil', 'reference_rates') }}
),

-- Bronze has a column per currency, as the file does; silver has a row per currency. Each
-- currency is its own select so the column-level lineage shows which bronze column feeds it.
-- FBIL quotes the yen per 100 yen, so it is converted to rupees per one yen by multiplying with
-- 0.01: DuckDB's division returns a floating point number, even for decimals, and rates stay
-- exact. The published figure and its quantity stay beside the converted rate, so the
-- conversion can be checked.
per_currency as (
    select
        rate_date,
        'USD' as currency,
        usd_inr as rate_inr,
        usd_inr as quoted_rate_inr,
        1 as quoted_units,
        _file_version,
        _source_file,
    from latest

    union all

    select
        rate_date,
        'GBP' as currency,
        gbp_inr as rate_inr,
        gbp_inr as quoted_rate_inr,
        1 as quoted_units,
        _file_version,
        _source_file,
    from latest

    union all

    select
        rate_date,
        'EUR' as currency,
        eur_inr as rate_inr,
        eur_inr as quoted_rate_inr,
        1 as quoted_units,
        _file_version,
        _source_file,
    from latest

    union all

    select
        rate_date,
        'JPY' as currency,
        jpy_inr_per_100 * 0.01 as rate_inr,
        jpy_inr_per_100 as quoted_rate_inr,
        100 as quoted_units,
        _file_version,
        _source_file,
    from latest
),

conformed as (
    select
        rate_date,
        currency,
        rate_inr::decimal(18, 6) as rate_inr,
        quoted_rate_inr,
        quoted_units,
        _file_version,
        _source_file,
    from per_currency
)

select * from conformed
