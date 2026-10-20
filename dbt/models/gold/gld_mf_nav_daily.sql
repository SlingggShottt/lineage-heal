with

-- Each scheme's NAV beside the one before it. The previous NAV is the scheme's last date with
-- a NAV, not necessarily the day before: schemes launch, close and skip days, and a report
-- that was never ingested leaves a gap. days_since_prev_nav says how wide that gap was.
with_previous as (
    select
        nav_date,
        scheme_code,
        scheme_name,
        plan,
        option_type,
        isin_div_payout_or_growth,
        isin_div_reinvestment,
        nav_inr,
        lag(nav_date) over scheme_history as prev_nav_date,
        lag(nav_inr) over scheme_history as prev_nav_inr
    from {{ ref('slv_amfi__nav') }}
    window scheme_history as (partition by scheme_code order by nav_date)
)

select
    nav_date,
    scheme_code,
    scheme_name,
    plan,
    option_type,
    isin_div_payout_or_growth,
    isin_div_reinvestment,
    nav_inr,
    prev_nav_date,
    prev_nav_inr,
    (nav_date - prev_nav_date)::integer as days_since_prev_nav,
    -- A scheme can publish a NAV of 0 on an ordinary day (segregated portfolios), and nothing
    -- can be compared with it, so a zero previous NAV gives no change. A NAV that falls to 0
    -- is reported as -100, which is what the feed says.
    ((nav_inr / nullif(prev_nav_inr, 0) - 1) * 100)::decimal(18, 4) as nav_change_pct
from with_previous
