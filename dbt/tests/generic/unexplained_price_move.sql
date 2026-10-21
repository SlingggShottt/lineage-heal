{#
    Fails for each recent price whose daily return is larger than a threshold although no
    corporate action explains it. For gld_equity_daily_prices, whose returns are already
    restated for splits and bonuses, so only unexplained moves remain.

    Two uses, because the certainty differs. A fall of more than 80% or a rise of more than
    400% with no corporate action is a change of scale (prices a hundred times too large or
    small): fail the build. A move beyond 25% in the main EQ series is beyond the widest
    price band the exchange allows, so it is probably real but deserves a look (a new listing,
    a stock back from suspension, an event the data does not model): warn.

    The rows are judged only for the latest `detection_days` dates in the data, so an old
    odd day cannot block the build for ever, and the clock is never read. Any corporate
    action on the date explains a move, because rights issues, demergers and buy-backs also
    move prices and are not adjusted for.

    Arguments: the largest normal rise and fall in percent (both positive numbers), how many
    of the latest dates to judge (default 3), and optionally the one series to look at.
#}
{% test unexplained_price_move(model, rise_pct, fall_pct, detection_days=3, series=none) %}

with recent as (
    select
        *,
        dense_rank() over (order by price_date desc) as recency
    from {{ model }}
    {% if series is not none %}
    where series = '{{ series }}'
    {% endif %}
)

select
    price_date,
    symbol,
    series,
    prev_close_price_inr,
    close_price_inr,
    action_factor,
    daily_return_pct
from recent
where
    recency <= {{ detection_days }}
    and not has_corporate_action
    and (daily_return_pct > {{ rise_pct }} or daily_return_pct < -{{ fall_pct }})

{% endtest %}
