-- The converted rate times the number of units it was quoted for must give back the published
-- figure. A change to the conversion (the yen divided by the wrong number, say) leaves every
-- other check passing, because the values stay positive and well formed; this one does not.
select
    rate_date,
    currency,
    rate_inr,
    quoted_rate_inr,
    quoted_units
from {{ ref('slv_fbil__reference_rates') }}
where rate_inr * quoted_units <> quoted_rate_inr
