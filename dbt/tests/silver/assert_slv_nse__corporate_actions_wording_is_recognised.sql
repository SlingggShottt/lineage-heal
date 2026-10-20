{{ config(severity='warn') }}

-- Actions the parser does not recognise. Only a warning: a few real ones (such as a scheme of
-- arrangement) are legitimately "other", but a sudden rise means the wording changed.
select
    ex_date,
    symbol,
    series,
    subject
from {{ ref('slv_nse__corporate_actions') }}
where action_type = 'other'
