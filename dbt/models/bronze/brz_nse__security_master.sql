with

-- The folder's trade date arrives as a partition column and is kept as _trade_date. The
-- file itself carries no date, so there is no date-match test for this dataset.
raw as (
    select
        * exclude (trade_date),
        trade_date::date as _trade_date,
    from {{ source('raw_nse', 'security_master') }}
),

-- NSE counts dates in seconds since 1980-01-01 and uses 0 for "not set". Values that do
-- not convert become null, which the column tests then report.
-- TODO(LH-014): add _ingested_at from the ingestion log; DuckDB cannot see file times.
typed as (
    select
        try_cast(trim("FinInstrmId") as bigint) as instrument_id,
        trim("TckrSymb") as symbol,
        trim("SctySrs") as series,
        trim("FinInstrmNm") as instrument_name,
        nullif(trim("ISIN"), '') as isin,
        try_cast(trim("NewBrdLotQty") as bigint) as board_lot_qty,
        -- Paise, as published. Silver converts to rupees once it can check the scale
        -- against corporate actions' face value.
        try_cast(trim("ParVal") as decimal(18, 2)) as par_value_paise,
        try_cast(trim("IssdCptl") as decimal(24, 0)) as issued_capital_shares,
        (timestamp '1980-01-01'
            + to_seconds(try_cast(nullif(trim("IsseDt"), '0') as bigint)))::date as issue_date,
        (timestamp '1980-01-01'
            + to_seconds(try_cast(nullif(trim("ListgDt"), '0') as bigint)))::date as listing_date,
        nullif(trim("SctyTpFlg"), '') as security_type_flag,
        _trade_date,
        filename as _source_file,
    from raw
)

select * from typed
