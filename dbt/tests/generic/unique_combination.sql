{#
    Fails for every combination of the given columns that appears more than once. Used for
    keys made of several columns, such as (scheme_code, nav_date).
#}
{% test unique_combination(model, columns) %}

select
    {{ columns | join(', ') }},
    count(*) as row_count
from {{ model }}
group by {{ columns | join(', ') }}
having count(*) > 1

{% endtest %}
