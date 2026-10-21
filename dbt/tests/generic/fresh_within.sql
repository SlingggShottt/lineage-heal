{#
    Fails when the newest date in a table is more than max_age_days before the run's reference
    date, or when the table is empty.

    The reference date is the as_of_date variable, supplied by the scheduled pipeline
    (`--vars '{as_of_date: 2026-10-16}'`). Without it the test does nothing. A development or
    demo database holds old data by design, and comparing it with today's date would fail every
    run, so the clock is never read here.

    Run it with severity warn: a table that is late is a signal for the Detector, not a reason
    to stop the layers downstream from building on the data that did arrive. A missing file on
    a day the market was open is judged against the exchange calendar (LH-016), not here.
#}
{% test fresh_within(model, date_column, max_age_days) %}

{% set as_of = var('as_of_date', none) %}

{% if as_of is none %}

select null::date as latest_date, null::date as as_of_date, null::integer as age_days
where false

{% else %}

select latest_date, as_of_date, as_of_date - latest_date as age_days
from (
    select max({{ date_column }}) as latest_date, cast('{{ as_of }}' as date) as as_of_date
    from {{ model }}
)
where latest_date is null or as_of_date - latest_date > {{ max_age_days }}

{% endif %}

{% endtest %}
