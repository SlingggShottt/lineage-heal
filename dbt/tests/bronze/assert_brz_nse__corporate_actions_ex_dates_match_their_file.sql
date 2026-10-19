-- The corporate actions API is asked for one ex-date at a time, so every record in a file
-- must carry that ex-date. A mismatch means the wrong day was fetched or NSE changed what
-- the date parameters select.
select
    symbol,
    series,
    subject,
    ex_date,
    _trade_date,
    _source_file
from {{ ref('brz_nse__corporate_actions') }}
where ex_date is not null and ex_date <> _trade_date
