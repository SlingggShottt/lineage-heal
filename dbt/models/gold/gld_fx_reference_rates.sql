with

-- Each currency's rate beside the one before it. The previous rate is the currency's last date
-- with a rate, not necessarily the day before: FBIL publishes on bank working days only, so a
-- Monday's previous rate is the Friday's, and a day that was never ingested leaves a wider gap.
-- days_since_prev_rate says how wide that gap was.
with_previous as (
    select
        rate_date,
        currency,
        rate_inr,
        lag(rate_date) over currency_history as prev_rate_date,
        lag(rate_inr) over currency_history as prev_rate_inr
    from {{ ref('slv_fbil__reference_rates') }}
    window currency_history as (partition by currency order by rate_date)
)

select
    rate_date,
    currency,
    rate_inr,
    prev_rate_date,
    prev_rate_inr,
    (rate_date - prev_rate_date)::integer as days_since_prev_rate,
    -- A percentage, not a money value, so it is worked out in floating point and stored as a
    -- decimal, as in gld_mf_nav_daily. The rates themselves stay exact decimals throughout.
    ((rate_inr / nullif(prev_rate_inr, 0) - 1) * 100)::decimal(18, 4) as rate_change_pct
from with_previous
