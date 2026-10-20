with

-- Totals cover every series the exchange publishes. Breadth and the median return use the EQ
-- series only (the ordinary rolling-settlement equities), because the other series are
-- thinly traded or restricted and would swamp the picture of how the market moved.
-- Returns come from the adjusted prices, so a split day is not counted as a crash. An
-- instrument with no previous close has no return and falls in none of the three buckets.
equity as (
    select
        price_date as summary_date,
        count(*) as equity_instruments,
        sum(volume)::bigint as equity_volume,
        sum(turnover_inr)::decimal(24, 2) as equity_turnover_inr,
        count(*) filter (where series = 'EQ' and daily_return_pct > 0) as eq_advancers,
        count(*) filter (where series = 'EQ' and daily_return_pct < 0) as eq_decliners,
        count(*) filter (where series = 'EQ' and daily_return_pct = 0) as eq_unchanged,
        (median(daily_return_pct) filter (where series = 'EQ'))::decimal(18, 4)
            as eq_median_return_pct
    from {{ ref('gld_equity_daily_prices') }}
    group by price_date
),

funds as (
    select
        nav_date as summary_date,
        count(*) as nav_schemes_published,
        median(nav_change_pct)::decimal(18, 4) as nav_median_change_pct
    from {{ ref('gld_mf_nav_daily') }}
    group by nav_date
)

-- A date with only one feed keeps its row, with the other feed's figures null rather than
-- zero: zero would claim that nothing traded. The two flags say which feeds are present, and
-- are what separates a weekend (no equity data, by design) from a missing file.
select
    coalesce(equity.summary_date, funds.summary_date) as summary_date,
    equity.summary_date is not null as has_equity_data,
    funds.summary_date is not null as has_nav_data,
    equity.equity_instruments,
    equity.equity_volume,
    equity.equity_turnover_inr,
    equity.eq_advancers,
    equity.eq_decliners,
    equity.eq_unchanged,
    equity.eq_median_return_pct,
    funds.nav_schemes_published,
    funds.nav_median_change_pct
from equity
full outer join funds on equity.summary_date = funds.summary_date
