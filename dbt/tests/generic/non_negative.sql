{#
    Fails for every row whose value is below zero. Zero itself passes: AMFI publishes a
    NAV of exactly 0 for segregated portfolios on ordinary days. Nulls are not this
    test's concern; combine it with not_null.
#}
{% test non_negative(model, column_name) %}

select {{ column_name }}
from {{ model }}
where {{ column_name }} < 0

{% endtest %}
