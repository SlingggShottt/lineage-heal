with

-- The folder's date is the ex-date the API was asked for; it is kept as _trade_date so each
-- record's own exDate can be checked against it.
raw as (
    select
        * exclude (trade_date),
        trade_date::date as _trade_date,
    from {{ source('raw_nse', 'corporate_actions') }}
),

-- NSE writes "-" where a field does not apply. Values that do not convert become null,
-- which the column tests then report.
-- TODO(LH-014): add _ingested_at from the ingestion log; DuckDB cannot see file times.
typed as (
    select
        trim(symbol) as symbol,
        trim(series) as series,
        nullif(nullif(trim(isin), ''), '-') as isin,
        trim(comp) as company_name,
        trim(subject) as subject,
        try_strptime(trim("exDate"), '%d-%b-%Y')::date as ex_date,
        try_strptime(trim("recDate"), '%d-%b-%Y')::date as record_date,
        try_strptime(trim("bcStartDate"), '%d-%b-%Y')::date as book_closure_start_date,
        try_strptime(trim("bcEndDate"), '%d-%b-%Y')::date as book_closure_end_date,
        try_strptime(trim("ndStartDate"), '%d-%b-%Y')::date as no_delivery_start_date,
        try_strptime(trim("ndEndDate"), '%d-%b-%Y')::date as no_delivery_end_date,
        try_cast(nullif(trim("faceVal"), '-') as decimal(18, 4)) as face_value_inr,
        nullif(trim(ind), '-') as indicator,
        _trade_date,
        filename as _source_file,
    from raw
)

select * from typed
