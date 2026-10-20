-- Gold adds a previous NAV and a change to silver NAVs but must not lose, invent or change a
-- published NAV. Every silver row has to appear exactly once with the same NAV, and nothing
-- else may appear. The window function that finds the previous NAV must not duplicate rows.
with silver as (
    select nav_date, scheme_code, nav_inr
    from {{ ref('slv_amfi__nav') }}
),

gold as (
    select nav_date, scheme_code, nav_inr
    from {{ ref('gld_mf_nav_daily') }}
)

select
    coalesce(silver.nav_date, gold.nav_date) as nav_date,
    coalesce(silver.scheme_code, gold.scheme_code) as scheme_code,
    silver.nav_inr as silver_nav_inr,
    gold.nav_inr as gold_nav_inr
from silver
full outer join gold
    on silver.nav_date = gold.nav_date
    and silver.scheme_code = gold.scheme_code
where
    silver.scheme_code is null
    or gold.scheme_code is null
    or silver.nav_inr is distinct from gold.nav_inr
