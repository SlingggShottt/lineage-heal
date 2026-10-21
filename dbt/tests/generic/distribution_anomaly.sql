{#
    Fails for each recent date whose typical (median) value of a column is far from that of
    recent dates of the same kind. Catches a change of unit or scale, such as prices in paise
    instead of rupees, that leaves every value a valid number. The median is used because it
    ignores a few extreme or damaged rows, which the row-level checks are there for. See the
    metric_anomalies macro for how dates are compared and what "recent" means.

    Arguments: the date column, then lookback, min_history, tolerance and detection_days as in
    the macro (defaults: 10, 5, 0.25 and 3).
#}
{% test distribution_anomaly(
    model, column_name, date_column, lookback=10, min_history=5, tolerance=0.25, detection_days=3
) %}

{% set metrics %}
    select {{ date_column }} as metric_date, median({{ column_name }}) as metric_value
    from {{ model }}
    where {{ date_column }} is not null
    group by {{ date_column }}
{% endset %}

{{ metric_anomalies(metrics, lookback, min_history, tolerance, detection_days) }}

{% endtest %}
