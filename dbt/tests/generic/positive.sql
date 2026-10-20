{#
    Fails for every row whose value is zero or below. For quantities that are ratios, such as a
    price adjustment factor, where zero would erase a price. Nulls are not this test's
    concern; combine it with not_null.
#}
{% test positive(model, column_name) %}

select {{ column_name }}
from {{ model }}
where {{ column_name }} <= 0

{% endtest %}
