-- A file stored under one trade date must hold the rates for that same date. A mismatch means
-- the wrong file was fetched, or the archive answered with a different date than the one asked
-- for. A date that did not parse is the not_null test's concern, not this one's.
select
    rate_date,
    _trade_date,
    _source_file
from {{ ref('brz_fbil__reference_rates') }}
where rate_date is not null and rate_date <> _trade_date
