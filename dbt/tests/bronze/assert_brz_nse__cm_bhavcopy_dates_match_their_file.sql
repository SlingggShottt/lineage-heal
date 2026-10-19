-- A file stored under one trade date must hold prices for that same date. A mismatch means
-- the wrong file was fetched, or NSE changed what a date's URL returns.
select
    symbol,
    series,
    trade_date,
    _trade_date,
    _source_file
from {{ ref('brz_nse__cm_bhavcopy') }}
where trade_date is not null and trade_date <> _trade_date
