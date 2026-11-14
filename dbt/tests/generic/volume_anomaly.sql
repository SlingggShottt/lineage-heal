{#
    Fails for each recent date whose row count is far from that of recent dates of the same
    kind. Catches a partial or empty file, or a duplicated one, that every value-level check
    would accept because each row in it is valid. See the metric_anomalies macro for how dates
    are compared and what "recent" means.

    Arguments: the date column, then lookback, min_history, tolerance and detection_days as in
    the macro (defaults: 10, 5, 0.25 and 3), and optionally `calendar: nse` to classify dates
    by the NSE calendar instead of the weekday.
#}
{% test volume_anomaly(
    model, date_column, lookback=10, min_history=5, tolerance=0.25, detection_days=3, calendar=none
) %}

{% set metrics %}
    select {{ date_column }} as metric_date, count(*) as metric_value
    from {{ model }}
    where {{ date_column }} is not null
    group by {{ date_column }}
{% endset %}

{#- The seed is referenced here, not in the macro, so dbt sees the dependency and builds it first. -#}
{% set calendar_seed = ref('seed_calendar_nse') if calendar == 'nse' else none %}

{{ metric_anomalies(metrics, lookback, min_history, tolerance, detection_days, calendar_seed) }}

{% endtest %}
