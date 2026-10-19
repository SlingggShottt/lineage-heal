-- Within a day the high is never below the low, and the close lies between them. Broken
-- ordering usually means columns were shifted or a price field was damaged. Nulls are
-- left to the not_null tests.
select
    symbol,
    series,
    trade_date,
    low_price_inr,
    high_price_inr,
    close_price_inr,
    _source_file
from {{ ref('brz_nse__cm_bhavcopy') }}
where
    high_price_inr < low_price_inr
    or close_price_inr < low_price_inr
    or close_price_inr > high_price_inr
