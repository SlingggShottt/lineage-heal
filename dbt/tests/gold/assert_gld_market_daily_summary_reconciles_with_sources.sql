-- The summary's counts and totals must equal what the silver tables say for the same date, and
-- its two presence flags must agree with whether each feed has rows. Silver is used rather than
-- the gold tables the summary is built from, so the check does not trust the model it checks.
-- A date missing from the summary, or present without a feed that has data, fails here.
with equity as (
    select
        price_date as summary_date,
        count(*) as instruments,
        sum(volume)::bigint as volume,
        sum(turnover_inr)::decimal(24, 2) as turnover_inr
    from {{ ref('slv_nse__equity_eod') }}
    group by price_date
),

funds as (
    select nav_date as summary_date, count(*) as schemes
    from {{ ref('slv_amfi__nav') }}
    group by nav_date
),

summary as (
    select * from {{ ref('gld_market_daily_summary') }}
)

select
    coalesce(summary.summary_date, equity.summary_date) as summary_date,
    'equity' as feed
from summary
full outer join equity on summary.summary_date = equity.summary_date
where
    summary.has_equity_data is distinct from (equity.summary_date is not null)
    or summary.equity_instruments is distinct from equity.instruments
    or summary.equity_volume is distinct from equity.volume
    or summary.equity_turnover_inr is distinct from equity.turnover_inr

union all

select
    coalesce(summary.summary_date, funds.summary_date) as summary_date,
    'nav' as feed
from summary
full outer join funds on summary.summary_date = funds.summary_date
where
    summary.has_nav_data is distinct from (funds.summary_date is not null)
    or summary.nav_schemes_published is distinct from funds.schemes
