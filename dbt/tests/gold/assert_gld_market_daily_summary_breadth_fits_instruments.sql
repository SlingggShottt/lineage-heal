-- Advancers, decliners and unchanged are disjoint groups of the instruments counted for the
-- day, so together they can never exceed that count. They fall short of it by the instruments
-- with no return (no previous close) and those in other series.
select
    summary_date,
    equity_instruments,
    eq_advancers + eq_decliners + eq_unchanged as eq_with_a_return
from {{ ref('gld_market_daily_summary') }}
where eq_advancers + eq_decliners + eq_unchanged > equity_instruments
