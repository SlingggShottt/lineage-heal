{#
    Fails for each recent NAV that moved by more than a threshold in one step. A fund's NAV
    is an average of many holdings, so it moves slowly: on real data the largest daily rise
    was 5% and the largest fall 13%, the latter a dividend payout. A rise of several hundred
    percent, or a fall of 80% to a still-positive NAV, is a change of scale. The same two uses
    as unexplained_price_move: wide limits that fail the build, and narrower ones that warn.

    A NAV of 0 is never judged. Segregated portfolios and matured schemes publish it on
    ordinary days, so a fall to zero says nothing about scale (the NAV monitor and the zero
    count watch for those). A move over a gap of several days is judged as it stands: the
    thresholds are far wider than a long weekend can explain.

    Arguments: the largest normal rise and fall in percent (both positive numbers), and how
    many of the latest dates to judge (default 3).
#}
{% test implausible_nav_move(model, rise_pct, fall_pct, detection_days=3) %}

with recent as (
    select
        *,
        dense_rank() over (order by nav_date desc) as recency
    from {{ model }}
)

select
    nav_date,
    scheme_code,
    scheme_name,
    prev_nav_inr,
    nav_inr,
    days_since_prev_nav,
    nav_change_pct
from recent
where
    recency <= {{ detection_days }}
    and nav_inr > 0
    and (nav_change_pct > {{ rise_pct }} or nav_change_pct < -{{ fall_pct }})

{% endtest %}
