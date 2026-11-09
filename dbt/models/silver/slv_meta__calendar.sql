with

-- Only feeds whose absence on a date is meaningful. NSE corporate actions are left out: the
-- loader asks by ex-date and stores nothing when the day has no actions, so a missing file is
-- normal there. The security master is a snapshot with no history, so no date is expected.
feeds as (
    select * from (
        values
            ('amfi', 'navall', 'every_day'),
            ('nse', 'cm_bhavcopy', 'nse_calendar')
    ) as t(source, dataset, calendar)
),

stored as (
    select distinct source, dataset, trade_date
    from {{ ref('brz_meta__raw_file_versions') }}
),

-- Judging starts at each feed's first stored date: a backfill that has not run yet is not a
-- run of missing files.
first_stored as (
    select feeds.source, feeds.dataset, feeds.calendar, min(stored.trade_date) as first_date
    from feeds
    inner join stored using (source, dataset)
    group by all
),

-- Judging ends at the date the run says it expects data for. Without one, the newest date any
-- feed has is the best available end, and never the wall clock, so a replay gives the same
-- answer on any day (as in fresh_within).
horizon as (
    {% if var('as_of_date', none) is not none -%}
    select cast('{{ var("as_of_date") }}' as date) as last_date
    {%- else -%}
    select max(trade_date) as last_date from stored
    {%- endif %}
),

spine as (
    select
        first_stored.source,
        first_stored.dataset,
        first_stored.calendar,
        cast(unnest(generate_series(first_stored.first_date, horizon.last_date, interval 1 day)) as date)
            as calendar_date
    from first_stored
    cross join horizon
),

-- The seed lists exceptions only; every other date follows the weekday rule.
classified as (
    select
        spine.source,
        spine.dataset,
        spine.calendar_date,
        case
            when spine.calendar = 'every_day' then 'every_day'
            when exceptions.status = 'closed' then 'holiday'
            when exceptions.status = 'special_session' then 'special_session'
            when dayofweek(spine.calendar_date) in (0, 6) then 'weekend'
            else 'weekday'
        end as expectation
    from spine
    left join {{ ref('seed_calendar_nse') }} as exceptions
        on spine.calendar = 'nse_calendar' and spine.calendar_date = exceptions.calendar_date
),

calendar as (
    select
        classified.source,
        classified.dataset,
        classified.calendar_date,
        classified.expectation,
        classified.expectation in ('every_day', 'weekday', 'special_session') as file_expected,
        stored.trade_date is not null as file_present
    from classified
    left join stored
        on classified.source = stored.source
        and classified.dataset = stored.dataset
        and classified.calendar_date = stored.trade_date
)

select * from calendar
