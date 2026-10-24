{#
    Dates whose value of a metric is far from what recent dates had.

    The base of the volume and distribution monitors (FR-WH-05). It is built for data that
    follows a publishing calendar, which is why it is not a plain z-score over the last N days:

    - Only dates that exist are compared. A holiday has no row, so it is a gap, not a zero that
      would stretch the spread and blind the monitor.
    - There are two patterns, weekdays and weekends, each with its own baseline. AMFI publishes
      about 8,700 schemes on a weekday and about 700 on a weekend, and both are normal.
    - A date is judged only if its own pattern has enough history, and is then compared with
      every pattern that has enough, being anomalous only if it is far from all of them. The
      other pattern can excuse a date but never condemn one. This matters on a market holiday that falls on a weekday: AMFI
      then publishes a weekend-sized report (714 schemes on Dussehra 2026, 92% below a weekday),
      which looks like a weekend rather than like a weekday. Matching either pattern accepts it,
      while a half-empty file matches neither and is still caught. The cost is that a weekday
      report truncated to weekend size is accepted too; telling those apart needs the exchange
      calendar (LH-016).
    - Each baseline is the median of the `lookback` previous dates of that pattern, so one bad
      day in the history barely moves it.
    - Only the latest `detection_days` dates in the data are judged, not the dates around a
      wall-clock "now". The same data gives the same answer today, in CI and in a benchmark
      replay of a past date.
    - Until `min_history` earlier dates of a pattern exist, that pattern is not used: a monitor
      with too little history says nothing rather than guess.

    Arguments: a query returning metric_date and metric_value; how many earlier dates form a
    baseline; how many are needed before one is used; the largest relative distance from a
    baseline that is normal (0.25 means plus or minus 25%); and how many of the latest dates
    to judge. Returns one row per date judged to be anomalous, with the nearest baseline.
#}
{% macro metric_anomalies(metrics, lookback, min_history, tolerance, detection_days) -%}
with metrics as (
    select
        metric_date,
        metric_value::double as metric_value,
        isodow(metric_date) >= 6 as is_weekend
    from ({{ metrics }})
),

-- How many dates of each pattern came before each date, and how recent the date is.
positions as (
    select
        metric_date,
        metric_value,
        is_weekend,
        row_number() over (order by metric_date desc) as recency,
        count(*) filter (where not is_weekend) over earlier as weekdays_before,
        count(*) filter (where is_weekend) over earlier as weekends_before
    from metrics
    window earlier as (order by metric_date rows between unbounded preceding and 1 preceding)
),

-- Each date's place among the dates of its own pattern.
numbered as (
    select
        metric_value,
        is_weekend,
        row_number() over (partition by is_weekend order by metric_date) as place
    from metrics
),

-- For every date and pattern: the median of that pattern's `lookback` dates just before it.
baselines as (
    select
        positions.metric_date,
        numbered.is_weekend as baseline_is_weekend,
        median(numbered.metric_value) as expected_value,
        count(*) as history_size
    from positions
    inner join numbered
        on (
            not numbered.is_weekend
            and numbered.place between positions.weekdays_before - {{ lookback }} + 1
                and positions.weekdays_before
        )
        or (
            numbered.is_weekend
            and numbered.place between positions.weekends_before - {{ lookback }} + 1
                and positions.weekends_before
        )
    group by positions.metric_date, numbered.is_weekend
    having count(*) >= {{ min_history }}
),

-- A date is as close to normal as its closest baseline says. It is only judged at all if the
-- baseline of its own pattern exists (has_own); the other pattern may then excuse it.
nearest as (
    select
        positions.metric_date,
        positions.metric_value,
        positions.recency,
        baselines.expected_value,
        baselines.history_size,
        bool_or(baselines.baseline_is_weekend = positions.is_weekend) over (
            partition by positions.metric_date
        ) as has_own,
        positions.metric_value / nullif(baselines.expected_value, 0) - 1 as relative_deviation,
        row_number() over (
            partition by positions.metric_date
            order by abs(positions.metric_value / nullif(baselines.expected_value, 0) - 1) nulls last
        ) as closeness
    from positions
    inner join baselines on baselines.metric_date = positions.metric_date
)

select
    metric_date,
    metric_value,
    expected_value,
    history_size,
    relative_deviation
from nearest
where
    closeness = 1
    and has_own
    and recency <= {{ detection_days }}
    and abs(relative_deviation) > {{ tolerance }}
{%- endmacro %}
