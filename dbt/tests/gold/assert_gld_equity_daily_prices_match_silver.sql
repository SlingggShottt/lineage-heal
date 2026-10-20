-- Gold may add columns to silver prices but must not lose, invent or change a published price.
-- Every silver row has to appear exactly once with the same close, and nothing else may appear.
-- A join that fans out or drops rows (for example from a corporate action matched twice) shows
-- up here as a mismatch.
with silver as (
    select price_date, symbol, series, close_price_inr
    from {{ ref('slv_nse__equity_eod') }}
),

gold as (
    select price_date, symbol, series, close_price_inr
    from {{ ref('gld_equity_daily_prices') }}
)

select
    coalesce(silver.price_date, gold.price_date) as price_date,
    coalesce(silver.symbol, gold.symbol) as symbol,
    coalesce(silver.series, gold.series) as series,
    silver.close_price_inr as silver_close_price_inr,
    gold.close_price_inr as gold_close_price_inr
from silver
full outer join gold
    on silver.price_date = gold.price_date
    and silver.symbol = gold.symbol
    and silver.series = gold.series
where
    silver.symbol is null
    or gold.symbol is null
    or silver.close_price_inr is distinct from gold.close_price_inr
