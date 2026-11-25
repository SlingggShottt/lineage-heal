with

-- The folder's trade date arrives as a partition column and is kept as _trade_date, so the
-- file's own date can be checked against it. A file holds exactly one date (D-82).
raw as (
    select
        * exclude (trade_date),
        trade_date::date as _trade_date,
    from {{ source('raw_fbil', 'reference_rates') }}
),

-- Rupees per one unit of the currency, except the yen, which FBIL quotes per 100 yen: the
-- column name says so, and silver converts it. Values that do not convert become null, which
-- the column tests then report.
typed as (
    select
        try_strptime(trim("Date"), '%d/%m/%Y')::date as rate_date,
        try_cast(trim("USD (INR / 1 USD)") as decimal(18, 4)) as usd_inr,
        try_cast(trim("GBP (INR / 1 GBP)") as decimal(18, 4)) as gbp_inr,
        try_cast(trim("EUR (INR / 1 EUR)") as decimal(18, 4)) as eur_inr,
        try_cast(trim("JPY (INR / 100 JPY)") as decimal(18, 4)) as jpy_inr_per_100,
        _trade_date,
        filename as _source_file,
    from raw
)

select * from typed
