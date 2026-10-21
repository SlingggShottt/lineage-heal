{#
    Dates whose value of a metric is far from what recent dates of the same kind had.

    The base of the volume and distribution monitors (FR-WH-05). It is built for data that
    follows a publishing calendar, which is why it is not a plain z-score over the last N days:

    - Only dates that exist are compared. A holiday has no row, so it is a gap, not a zero that
      would stretch the spread and blind the monitor.
    - Weekdays and weekends are compared separately. AMFI publishes about 8,700 schemes on a
      weekday and about 650 on a weekend, and both are normal.
    - The baseline is the median of the `lookback` previous dates of the same kind, so one bad
      day in the history barely moves it.
    - Only the latest `detection_days` dates in the data are judged, not the dates around a
      wall-clock "now". The same data gives the same answer today, in CI and in a benchmark
      replay of a past date.
    - Until `min_history` earlier dates of the same kind exist, nothing is judged: a monitor
      with too little history says nothing rather than guess.

    Arguments: a query returning metric_date and metric_value; how many earlier dates form the
    baseline; how many are needed before judging; the largest relative distance from the
    baseline that is normal (0.25 means plus or minus 25%); and how many of the latest dates
    to judge. Returns one row per date judged to be anomalous.
#}
{% macro metric_anomalies(metrics, lookback, min_history, tolerance, detection_days) -%}
with metrics as (
    select
        metric_date,
        metric_value::double as metric_value,
        isodow(metric_date) >= 6 as is_weekend
    from ({{ metrics }})
),

baselined as (
    select
        *,
        median(metric_value) over previous as expected_value,
        count(metric_value) over previous as history_size,
        row_number() over (order by metric_date desc) as recency
    from metrics
    window previous as (
        partition by is_weekend
        order by metric_date
        rows between {{ lookback }} preceding and 1 preceding
    )
)

select
    metric_date,
    metric_value,
    expected_value,
    history_size,
    metric_value / nullif(expected_value, 0) - 1 as relative_deviation
from baselined
where
    recency <= {{ detection_days }}
    and history_size >= {{ min_history }}
    and abs(metric_value / nullif(expected_value, 0) - 1) > {{ tolerance }}
{%- endmacro %}
