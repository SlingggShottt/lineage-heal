-- Within a day the high is never below the low, and the open, the close and the last traded
-- price all lie between them, because each is a price at which the instrument traded. Broken
-- ordering usually means columns were shifted or a price field was damaged. Nulls are
-- left to the not_null tests. On 22,049 real rows no price was outside the range.
select
    symbol,
    series,
    trade_date,
    low_price_inr,
    high_price_inr,
    open_price_inr,
    close_price_inr,
    last_price_inr,
    _source_file
from {{ ref('brz_nse__cm_bhavcopy') }}
where
    high_price_inr < low_price_inr
    or open_price_inr < low_price_inr
    or open_price_inr > high_price_inr
    or close_price_inr < low_price_inr
    or close_price_inr > high_price_inr
    or last_price_inr < low_price_inr
    or last_price_inr > high_price_inr
