-- A report stored under one trade date must hold NAVs for that same date. A mismatch
-- means the wrong file was fetched, or AMFI changed what a date query returns.
select
    scheme_code,
    nav_date,
    _trade_date,
    _source_file
from {{ ref('brz_amfi__navall') }}
where nav_date is not null and nav_date <> _trade_date
