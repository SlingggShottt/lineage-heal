-- An instrument that traded has a price. A zero close beside a positive volume means the
-- price fields were zeroed or lost while the volume survived, which the range check cannot
-- see when every price is zero, and which "not negative" accepts. On 22,049 real rows no
-- traded instrument had a zero close.
select
    symbol,
    series,
    trade_date,
    close_price_inr,
    volume,
    _source_file
from {{ ref('brz_nse__cm_bhavcopy') }}
where volume > 0 and close_price_inr = 0
