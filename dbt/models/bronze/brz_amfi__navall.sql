with

raw as (
    select * from {{ source('raw_amfi', 'navall') }}
),

-- The reports interleave data rows with blank lines, category headings and fund-house
-- names. Those lines have a value only in the first column, and it is not a scheme code.
-- Keeping every row that has a scheme code or any other value means a damaged data row
-- stays visible to the tests instead of being dropped here.
data_rows as (
    select * from raw
    where
        try_cast(trim("Scheme Code") as bigint) is not null
        or coalesce(
            "NAV Name",
            "Plan",
            "Option",
            "ISIN Div Payout/ISIN Growth",
            "ISIN Div Reinvestment",
            "Net Asset Value",
            "Date"
        ) is not null
),

-- Values that do not convert become null, which the column tests then report.
-- TODO(LH-014): add _ingested_at from the ingestion log; DuckDB cannot see file times.
typed as (
    select
        try_cast(trim("Scheme Code") as bigint) as scheme_code,
        trim("NAV Name") as scheme_name,
        nullif(trim("Plan"), '') as plan_name,
        nullif(trim("Option"), '') as option_name,
        nullif(nullif(trim("ISIN Div Payout/ISIN Growth"), ''), '-') as isin_div_payout_or_growth,
        nullif(nullif(trim("ISIN Div Reinvestment"), ''), '-') as isin_div_reinvestment,
        try_cast(trim("Net Asset Value") as decimal(18, 4)) as nav_inr,
        try_strptime(trim("Date"), '%d-%b-%Y')::date as nav_date,
        trade_date::date as _trade_date,
        filename as _source_file,
    from data_rows
)

select * from typed
