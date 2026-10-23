{{ config(severity='warn') }}

-- Actions the parser does not recognise, among the latest three ex-dates. Only a warning: a few
-- real ones (a scheme of arrangement, a rights issue of preference shares and warrants) are
-- legitimately "other". Judging every date would warn on every build for ever over those; a
-- change in NSE's wording shows up in new actions, and so in the latest dates, instead.
with recent as (
    select
        *,
        dense_rank() over (order by ex_date desc) as recency
    from {{ ref('slv_nse__corporate_actions') }}
)

select
    ex_date,
    symbol,
    series,
    subject
from recent
where action_type = 'other' and recency <= 3
