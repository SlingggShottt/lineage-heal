with

-- The folder's trade date arrives as a partition column; it is kept as _trade_date so the
-- file's own TradDt can be checked against it.
raw as (
    select
        * exclude (trade_date),
        trade_date::date as _trade_date,
    from {{ source('raw_nse', 'cm_bhavcopy') }}
),

-- Values that do not convert become null, which the column tests then report.
-- TODO(LH-014): add _ingested_at from the ingestion log; DuckDB cannot see file times.
typed as (
    select
        try_cast(trim("FinInstrmId") as bigint) as instrument_id,
        trim("TckrSymb") as symbol,
        trim("SctySrs") as series,
        nullif(trim("ISIN"), '') as isin,
        trim("FinInstrmNm") as instrument_name,
        try_cast(trim("OpnPric") as decimal(18, 4)) as open_price_inr,
        try_cast(trim("HghPric") as decimal(18, 4)) as high_price_inr,
        try_cast(trim("LwPric") as decimal(18, 4)) as low_price_inr,
        try_cast(trim("ClsPric") as decimal(18, 4)) as close_price_inr,
        try_cast(trim("LastPric") as decimal(18, 4)) as last_price_inr,
        try_cast(trim("PrvsClsgPric") as decimal(18, 4)) as prev_close_price_inr,
        try_cast(trim("SttlmPric") as decimal(18, 4)) as settlement_price_inr,
        try_cast(trim("TtlTradgVol") as bigint) as volume,
        try_cast(trim("TtlTrfVal") as decimal(20, 2)) as turnover_inr,
        try_cast(trim("TtlNbOfTxsExctd") as bigint) as trades,
        try_cast(trim("TradDt") as date) as trade_date,
        _trade_date,
        filename as _source_file,
    from raw
)

select * from typed
